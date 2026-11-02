from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from app.core.config import ATTACHMENT_ROOT
from app.repositories.document_validation_batch import DocumentValidationBatchRepository
from app.services.document_validation import DocumentValidationService

logger = logging.getLogger(__name__)

HANDOVER_FILE = re.compile(r"^.*_HandoverDocument_.*$", re.IGNORECASE)


def utc_now_naive() -> datetime:
    # SQL columns are DATETIME (without timezone); store UTC consistently.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DocumentValidationBatchService:
    """Validate newly submitted handover attachments from dbo.Ideas."""

    def __init__(
        self,
        repository: Optional[DocumentValidationBatchRepository] = None,
        validator: Optional[DocumentValidationService] = None,
    ) -> None:
        self.repository = repository or DocumentValidationBatchRepository()
        self.validator = validator or DocumentValidationService()
        self.attachment_root = Path(ATTACHMENT_ROOT).expanduser()

    def run(self) -> Dict[str, int]:
        stats = {
            "total_ideas": 0,
            "processed_ideas": 0,
            "failed_ideas": 0,
            "skipped_ideas": 0,
            "total_documents": 0,
            "processed_documents": 0,
            "skipped_documents": 0,
            "valid_documents": 0,
            "invalid_documents": 0,
        }
        now = utc_now_naive()
        run_id = self.repository.start_run(now)
        try:
            ideas = self.repository.get_pending_handover_documents()
            stats["total_ideas"] = len(ideas)
            for idea in ideas:
                idea_id = int(idea["idea_id"])
                files = self._split_attachment_files(idea["attachment_files"])
                handovers = [path for path in files if HANDOVER_FILE.match(Path(path).name)]
                if not handovers:
                    stats["skipped_ideas"] += 1
                    continue

                idea_processed = False
                idea_failed = False
                for stored_path in handovers:
                    file_name = Path(stored_path).name
                    stats["total_documents"] += 1
                    if self.repository.is_completed(idea_id, file_name):
                        stats["skipped_documents"] += 1
                        continue

                    file_path = self._resolve_path(stored_path)
                    if not file_path.is_file():
                        reason = f"File tidak ditemukan: {file_path}"
                        self._save_error(idea_id, file_name, reason)
                        idea_failed = True
                        logger.error("[HANDOVER VALIDATION] %s", reason)
                        continue

                    if file_path.suffix.lower() != ".pdf":
                        self.repository.save_result(
                            idea_id=idea_id, file_name=file_name, status="unsupported",
                            is_valid=False, reason="Format dokumen belum didukung; validator saat ini hanya menerima PDF.",
                            now=utc_now_naive(),
                        )
                        stats["skipped_documents"] += 1
                        continue

                    try:
                        content = file_path.read_bytes()
                        result, _, _, _ = self.validator.validate_pdf(content)
                        valid = bool(result.is_supported)
                        self.repository.save_result(
                            idea_id=idea_id,
                            file_name=file_name,
                            status="valid" if valid else "invalid",
                            is_valid=valid,
                            percentage=round(result.match_percentage * 100, 2),
                            matched=result.matched_count,
                            total=result.total_markers,
                            reason=result.reason,
                            now=utc_now_naive(),
                        )
                        stats["processed_documents"] += 1
                        stats["valid_documents" if valid else "invalid_documents"] += 1
                        idea_processed = True
                    except Exception as ex:
                        reason = f"{type(ex).__name__}: {ex}"
                        self._save_error(idea_id, file_name, reason)
                        idea_failed = True
                        logger.exception("[HANDOVER VALIDATION] Gagal memproses %s", file_name)

                if idea_processed:
                    stats["processed_ideas"] += 1
                elif idea_failed:
                    stats["failed_ideas"] += 1
                else:
                    stats["skipped_ideas"] += 1

            self.repository.finish_run(run_id, utc_now_naive(), stats)
            logger.info("[HANDOVER VALIDATION] Batch selesai: %s", stats)
            return stats
        except Exception as ex:
            self.repository.finish_run(run_id, utc_now_naive(), stats, str(ex))
            raise

    @staticmethod
    def _split_attachment_files(value: str) -> list[str]:
        # Older rows use commas; newer rows commonly use semicolons.
        return [item.strip().strip('"') for item in re.split(r"[;,]", value or "") if item.strip()]

    def _resolve_path(self, stored_path: str) -> Path:
        path = Path(stored_path.strip().replace("/", "\\"))
        if path.is_absolute():
            return path
        configured_path = (self.attachment_root / path).resolve()
        if configured_path.is_file():
            return configured_path
        # Some deployments store paths relative to the application root.
        return (Path.cwd() / path).resolve()

    def _save_error(self, idea_id: int, file_name: str, reason: str) -> None:
        self.repository.save_result(
            idea_id=idea_id,
            file_name=file_name,
            status="error",
            is_valid=False,
            reason=reason[:1000],
            now=utc_now_naive(),
        )
