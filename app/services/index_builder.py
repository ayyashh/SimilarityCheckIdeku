"""
IndexBuilder
============
Business Aggregator: menggabungkan data dari IdeaRepository (data Idea di
database) dengan hasil AttachmentService (ekstraksi attachment di local
storage) menjadi satu object IndexBuildResult yang siap dikonsumsi oleh
EmbeddingService, lalu nantinya oleh IndexerRunner.

IndexBuilder TIDAK melakukan:
- embedding / mengetahui model AI
- penyimpanan ke database (termasuk penyimpanan embedding)
- OCR / ekstraksi file (itu tanggung jawab AttachmentService)

IndexBuilder juga tidak menampilkan/meneruskan log internal milik
dependency-nya (AttachmentService) ke pemanggilnya -- lihat catatan pada
_run_attachment_service() di bawah.
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.repositories.ideas import IdeaRepository
from app.services.attachment_service import AttachmentService, AttachmentResult

logger = logging.getLogger(__name__)


# ======================================================================
# EMBEDDING SOURCE
# ======================================================================


class EmbeddingSource:

    IDEA_ONLY = "IDEA_ONLY"
    IDEA_ATTACHMENT = "IDEA_ATTACHMENT"


# ======================================================================
# RESULT OBJECT
# ======================================================================


@dataclass
class IndexBuildResult:
    """
    Representasi lengkap 1 Idea, hasil business aggregation antara
    IdeaRepository + AttachmentService. Inilah kontrak yang dikonsumsi
    EmbeddingService (lewat .combined_text) dan IndexerRunner (lewat
    seluruh field lainnya untuk keperluan log & penyimpanan metadata).
    """

    idea_id: Optional[int]
    idea_code: str
    idea_found: bool
    idea_text: str
    attachment_text: str
    combined_text: str
    embedding_source: str
    attachment_found: bool
    attachment_count: int
    attachment_files: List[str] = field(default_factory=list)
    file_results: List[Any] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    ocr_warnings: List[str] = field(default_factory=list)
    extraction_success: int = 0
    extraction_failed: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    handover_document_found: bool = False
    business_case_document_found: bool = False

    def to_dict(self) -> Dict[str, Any]:

        return {
            "IdeaId": self.idea_id,
            "IdeaCode": self.idea_code,
            "IdeaFound": self.idea_found,
            "IdeaText": self.idea_text,
            "AttachmentText": self.attachment_text,
            "CombinedText": self.combined_text,
            "EmbeddingSource": self.embedding_source,
            "AttachmentFound": self.attachment_found,
            "AttachmentCount": self.attachment_count,
            "AttachmentFiles": self.attachment_files,
            "Warnings": self.warnings,
            "OCRWarnings": self.ocr_warnings,
            "ExtractionSuccess": self.extraction_success,
            "ExtractionFailed": self.extraction_failed,
            "FilesProcessed": self.files_processed,
            "FilesSkipped": self.files_skipped,
            "HandoverDocumentFound": self.handover_document_found,
            "BusinessCaseDocumentFound": self.business_case_document_found,
        }


# ======================================================================
# INDEX BUILDER
# ======================================================================


class IndexBuilder:
    """
    Responsibilities
    ----------------
    1. Menormalisasi idea_code ke 2 representasi:
       - numeric_code  ("004837")      -> dipakai AttachmentService
                                           (nama file: IMS-004837-xxxxx.ext)
       - db_code       ("IMS-004837")  -> dipakai query IdeaRepository
                                           (kolom Ideas.IdeaCode tersimpan
                                           lengkap dengan prefix "IMS-")
    2. Mengambil IdeaName, Background, Solution dari IdeaRepository
    3. Memanggil AttachmentService untuk mendapatkan AttachmentText + metadata
    4. Menyusun CombinedText dengan urutan tetap:
       Idea Name -> Background -> Solution -> Attachment (opsional)
    5. Menentukan EmbeddingSource (IDEA_ONLY / IDEA_ATTACHMENT)
    6. Menghasilkan IndexBuildResult lengkap untuk EmbeddingService & IndexerRunner
    """

    # Menerima input dengan ATAU tanpa prefix "IMS-", selama diikuti angka.
    # Contoh valid: "004837" ATAU "IMS-004837" ATAU "ims-004837"
    IDEA_CODE_PATTERN = re.compile(r"^(?:IMS-)?(\d+)$", re.IGNORECASE)

    def __init__(
        self,
        idea_repository: Optional[IdeaRepository] = None,
        attachment_service: Optional[AttachmentService] = None,
    ):

        # Dependency injection -> memudahkan unit test dengan mock repository/service
        self.idea_repository = idea_repository or IdeaRepository()
        self.attachment_service = attachment_service or AttachmentService()

    # ==================================================================
    # IDEA CODE NORMALIZATION
    # ==================================================================

    def _split_idea_code(self, raw_idea_code: str) -> Tuple[str, str]:
        """
        Return (numeric_code, db_code).

        numeric_code -> untuk AttachmentService (yang sendiri sudah
                         menambahkan prefix "IMS-" saat mencari file)
        db_code       -> untuk IdeaRepository.get_by_code(), karena
                         kolom Ideas.IdeaCode tersimpan lengkap
                         dengan prefix "IMS-"
        """

        cleaned = (raw_idea_code or "").strip()

        match = self.IDEA_CODE_PATTERN.match(cleaned)

        if not match:

            logger.warning(
                f"[IDEA CODE FORMAT TIDAK DIKENALI] '{raw_idea_code}' "
                f"-> dipakai apa adanya tanpa normalisasi"
            )

            return cleaned, cleaned

        numeric_code = match.group(1)

        db_code = f"IMS-{numeric_code}"

        return numeric_code, db_code

    # ==================================================================
    # MAIN ENTRY POINT
    # ==================================================================

    def build(self, idea_code: str) -> IndexBuildResult:
        """
        Entry point utama. Selalu mengembalikan IndexBuildResult,
        tidak pernah raise, supaya IndexerRunner bisa lanjut ke idea
        berikutnya walaupun 1 idea gagal diproses.

        idea_code boleh dikirim dengan ATAU tanpa prefix "IMS-"
        (mis. "004837" atau "IMS-004837") -> akan dinormalisasi
        otomatis sebelum dipakai ke masing-masing dependency.
        """

        numeric_code, db_code = self._split_idea_code(idea_code)

        idea = self._fetch_idea(db_code)

        if idea is None:

            logger.warning(f"[IDEA NOT FOUND] {db_code}")

            return IndexBuildResult(
                idea_id=None,
                idea_code=db_code,
                idea_found=False,
                idea_text="",
                attachment_text="",
                combined_text="",
                embedding_source=EmbeddingSource.IDEA_ONLY,
                attachment_found=False,
                attachment_count=0,
                warnings=["Idea Not Found"],
            )

        idea_text = self._compose_idea_text(idea)

        attachment_result = self._run_attachment_service(numeric_code)

        combined_text = self._compose_combined_text(
            idea_text=idea_text, attachment_text=attachment_result.attachment_text
        )

        embedding_source = (
            EmbeddingSource.IDEA_ATTACHMENT
            if attachment_result.attachment_text.strip()
            else EmbeddingSource.IDEA_ONLY
        )

        return IndexBuildResult(
            idea_id=self._get_field(idea, "IdeaId"),
            idea_code=db_code,
            idea_found=True,
            idea_text=idea_text,
            attachment_text=attachment_result.attachment_text,
            combined_text=combined_text,
            embedding_source=embedding_source,
            attachment_found=attachment_result.attachment_found,
            attachment_count=attachment_result.attachment_count,
            attachment_files=attachment_result.attachment_files,
            file_results=attachment_result.file_results,
            warnings=attachment_result.warnings,
            ocr_warnings=attachment_result.ocr_warnings,
            extraction_success=attachment_result.extraction_success,
            extraction_failed=attachment_result.extraction_failed,
            files_processed=attachment_result.files_processed,
            files_skipped=attachment_result.files_skipped,
            handover_document_found=attachment_result.handover_document_found,
            business_case_document_found=attachment_result.business_case_document_found,
        )

    # ==================================================================
    # ATTACHMENT SERVICE CALL (isolasi output internal dependency)
    # ==================================================================

    def _run_attachment_service(self, numeric_code: str) -> AttachmentResult:
        """
        Memanggil AttachmentService.process().

        AttachmentService punya log internal (mis. daftar file kandidat
        untuk keperluan debug dirinya sendiri) yang BUKAN tanggung jawab
        IndexBuilder untuk ditampilkan -- itu milik pengujian
        AttachmentService, bukan pengujian IndexBuilder. stdout dari
        pemanggilan ini di-redirect ke buffer sementara supaya batas
        tanggung jawab antar modul tetap jelas di lapisan manapun
        IndexBuilder dipakai (test script maupun IndexerRunner nanti),
        tanpa perlu mengubah kode AttachmentService itu sendiri.
        """

        with contextlib.redirect_stdout(io.StringIO()):

            return self.attachment_service.process(numeric_code)

    # ==================================================================
    # IDEA FETCH (defensif terhadap bentuk return repository: dict / object)
    # ==================================================================

    def _fetch_idea(self, db_code: str):

        try:

            return self.idea_repository.get_by_code(db_code)

        except Exception as ex:

            logger.error(f"[REPOSITORY FAILED] {db_code} -> {ex}")

            return None

    @staticmethod
    def _get_field(idea, field_name: str, default: str = "") -> Any:
        """
        Mendukung idea berupa dict ATAU object (attribute-based),
        supaya IndexBuilder tidak terikat pada 1 bentuk return repository.
        """

        if isinstance(idea, dict):
            return idea.get(field_name, default)

        return getattr(idea, field_name, default)

    # ==================================================================
    # TEXT COMPOSITION
    # ==================================================================

    def _compose_idea_text(self, idea) -> str:
        """
        Menyusun IdeaText dengan urutan tetap: Idea Name -> Background -> Solution.
        """

        idea_name = str(self._get_field(idea, "IdeaName", "")).strip()

        background = str(self._get_field(idea, "Background", "")).strip()

        solution = str(self._get_field(idea, "Solution", "")).strip()

        sections = [
            f"Idea Name\n{idea_name}",
            f"Background\n{background}",
            f"Solution\n{solution}",
        ]

        return "\n\n".join(sections)

    def _compose_combined_text(self, idea_text: str, attachment_text: str) -> str:
        """
        Menyusun CombinedText: IdeaText, lalu Attachment (jika ada).
        Kalau attachment kosong, CombinedText == IdeaText (EmbeddingSource
        otomatis menjadi IDEA_ONLY di build()).
        """

        attachment_text = attachment_text.strip()

        if not attachment_text:
            return idea_text

        return f"{idea_text}\n\nAttachment\n{attachment_text}"
