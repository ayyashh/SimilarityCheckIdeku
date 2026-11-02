from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from app.repositories.database import build_connection_string, safe_connection


class DocumentValidationBatchRepository:
    """Database operations for scheduled handover document validation."""

    def __init__(self) -> None:
        self.connection_string = build_connection_string()

    def get_pending_handover_documents(self) -> List[Dict[str, Any]]:
        sql = """
        SELECT i.Id AS IdeaId, i.IdeaCode, i.AttachmentFiles
        FROM dbo.Ideas AS i
        WHERE i.IsDeleted = 0
          AND i.AttachmentFiles IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM STRING_SPLIT(REPLACE(i.AttachmentFiles, ';', ','), ',') AS f
              WHERE LTRIM(RTRIM(f.value)) LIKE '%[_]HandoverDocument[_]%'
          )
        ORDER BY i.Id
        """
        with safe_connection(self.connection_string) as conn:
            rows = conn.cursor().execute(sql).fetchall()
        return [
            {
                "idea_id": row.IdeaId,
                "idea_code": row.IdeaCode,
                "attachment_files": row.AttachmentFiles or "",
            }
            for row in rows
        ]

    def is_completed(self, idea_id: int, file_name: str) -> bool:
        sql = """
        SELECT TOP (1) 1
        FROM dbo.IdeaDocumentValidation
        WHERE IdeaId = ? AND FileName = ?
          AND Status IN ('valid', 'invalid', 'unsupported')
        """
        with safe_connection(self.connection_string) as conn:
            row = conn.cursor().execute(sql, idea_id, file_name).fetchone()
        return row is not None

    def save_result(
        self,
        *,
        idea_id: int,
        file_name: str,
        status: str,
        is_valid: bool,
        percentage: float = 0,
        matched: int = 0,
        total: int = 0,
        reason: str = "",
        now: datetime,
    ) -> None:
        sql = """
        UPDATE dbo.IdeaDocumentValidation
        SET DocumentType = 'HANDOVER', Status = ?, IsValid = ?,
            MatchPercentage = ?, MatchedParameters = ?, TotalParameters = ?,
            Reason = ?, UpdatedDate = ?
        WHERE IdeaId = ? AND FileName = ?
        IF @@ROWCOUNT = 0
        BEGIN
            INSERT INTO dbo.IdeaDocumentValidation
                (IdeaId, DocumentType, FileName, Status, IsValid,
                 MatchPercentage, MatchedParameters, TotalParameters,
                 CreatedDate, UpdatedDate, Reason)
            VALUES (?, 'HANDOVER', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        END
        """
        values = (
            status, is_valid, percentage, matched, total, reason[:1000], now,
            idea_id, file_name,
            idea_id, file_name, status, is_valid, percentage, matched, total,
            now, now, reason[:1000],
        )
        with safe_connection(self.connection_string) as conn:
            conn.cursor().execute(sql, *values)

    def start_run(self, now: datetime) -> int:
        sql = """
        INSERT INTO dbo.IdeaDocumentValidationHistory
            (StartTime, Status, TotalIdeas, ProcessedIdeas, FailedIdeas,
             SkippedIdeas, TotalDocuments, ProcessedDocuments, SkippedDocuments,
             ValidDocuments, InvalidDocuments, CreatedDate)
        OUTPUT INSERTED.Id
        VALUES (?, 'RUNNING', 0, 0, 0, 0, 0, 0, 0, 0, 0, ?)
        """
        with safe_connection(self.connection_string) as conn:
            row = conn.cursor().execute(sql, now, now).fetchone()
        return int(row[0])

    def finish_run(self, run_id: int, now: datetime, stats: Dict[str, int], error: str = "") -> None:
        status = "FAILED" if error else "COMPLETED"
        sql = """
        UPDATE dbo.IdeaDocumentValidationHistory
        SET FinishTime = ?, Status = ?, TotalIdeas = ?, ProcessedIdeas = ?,
            FailedIdeas = ?, SkippedIdeas = ?, TotalDocuments = ?,
            ProcessedDocuments = ?, SkippedDocuments = ?, ValidDocuments = ?,
            InvalidDocuments = ?, ErrorMessage = ?
        WHERE Id = ?
        """
        with safe_connection(self.connection_string) as conn:
            conn.cursor().execute(
                sql,
                now, status, stats["total_ideas"], stats["processed_ideas"],
                stats["failed_ideas"], stats["skipped_ideas"],
                stats["total_documents"], stats["processed_documents"],
                stats["skipped_documents"], stats["valid_documents"],
                stats["invalid_documents"], error[:4000] or None, run_id,
            )
