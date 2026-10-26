"""
AttachmentService
=================
Bertanggung jawab penuh atas siklus hidup attachment 1 Idea:
mencari file, memilih stage terbesar, mengurutkan, melakukan
ekstraksi lewat Dispatcher, menggabungkan hasil, dan mencatat
warning per kategori tanpa pernah menghentikan proses indexing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any
import logging
import re

from app.core.config import ATTACHMENT_ROOT
from app.dispatcher.attachment_dispatcher import AttachmentDispatcher

logger = logging.getLogger(__name__)


# ======================================================================
# WARNING CATEGORIES
# ======================================================================
# Konstanta string dipakai (bukan Enum) supaya langsung serializable
# ke JSON/log tanpa konversi tambahan, dan gampang dibandingkan.
# ======================================================================


class AttachmentWarning:

    ATTACHMENT_MISSING = "Attachment Missing"
    OCR_FAILED = "OCR Failed"
    UNSUPPORTED_FILE = "Unsupported File"
    EMPTY_EXTRACTION = "Empty Extraction"
    CORRUPTED_FILE = "Corrupted File"
    DISPATCHER_FAILED = "Dispatcher Failed"
    DOCUMENT_TYPE_NOT_ALLOWED = "Document Type Not Allowed"
    DOCUMENT_VALIDATION_UNSUPPORTED_FILE = "Document Validation Unsupported File"


# ======================================================================
# DATA CLASSES
# ======================================================================


@dataclass
class AttachmentFileInfo:
    """
    Metadata hasil parsing nama file.

    Mendukung 2 format:
    - Format baru  : IMS-004837_S2_001.pptx  -> stage=2 (current stage idea)
    - Format lama  : IMS-004837-432531.pdf   -> stage=-1 (penanda format lama,
                      belum ada info stage eksplisit di nama file)
    """

    idea_code: str
    stage: int
    order: int
    path: Path

    @property
    def file_name(self) -> str:
        return self.path.name

    @property
    def is_legacy_format(self) -> bool:
        return self.stage == -1


@dataclass
class AttachmentFileResult:
    """Hasil ekstraksi dan validasi 1 file attachment."""

    file_name: str
    file_path: str
    stage: int
    order: int
    success: bool
    text: str = ""
    warning: Optional[str] = None
    reason: Optional[str] = None
    document_type: Optional[str] = None
    match_percentage: float = 0.0
    matched_count: int = 0
    total_markers: int = 0

    def to_dict(self) -> Dict[str, Any]:

        return {
            "FileName": self.file_name,
            "FilePath": self.file_path,
            "Stage": self.stage,
            "Order": self.order,
            "Success": self.success,
            "Warning": self.warning,
            "DocumentType": self.document_type,
        }


@dataclass
class AttachmentResult:
    """
    Object lengkap hasil pemrosesan attachment 1 Idea.
    Inilah kontrak yang dikonsumsi langsung oleh IndexBuilder / IndexerRunner
    -- IndexerRunner tidak perlu tahu detail dispatcher/OCR sama sekali.
    """

    idea_code: str
    attachment_found: bool
    selected_stage: Optional[int]
    attachment_count: int
    attachment_files: List[str] = field(default_factory=list)
    attachment_text: str = ""
    warnings: List[str] = field(default_factory=list)
    ocr_warnings: List[str] = field(default_factory=list)
    extraction_success: int = 0
    extraction_failed: int = 0
    file_results: List[AttachmentFileResult] = field(default_factory=list)

    # ------------------------------------------------------------
    # Serialization helper -> dipakai untuk logging / API response
    # ------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:

        return {
            "IdeaCode": self.idea_code,
            "AttachmentFound": self.attachment_found,
            "SelectedStage": self.selected_stage,
            "AttachmentCount": self.attachment_count,
            "AttachmentFiles": self.attachment_files,
            "AttachmentText": self.attachment_text,
            "Warnings": self.warnings,
            "OCRWarnings": self.ocr_warnings,
            "ExtractionSuccess": self.extraction_success,
            "ExtractionFailed": self.extraction_failed,
        }

    @property
    def files_processed(self) -> int:
        """Files whose extracted text was accepted by the active validator."""

        return sum(1 for result in self.file_results if result.success)

    @property
    def files_skipped(self) -> int:
        """Files found for the Idea but not included in AttachmentText."""

        return len(self.file_results) - self.files_processed

    @property
    def handover_document_found(self) -> bool:

        return any(
            result.success
            and (result.document_type or "").strip().lower() == "handover"
            for result in self.file_results
        )

    @property
    def business_case_document_found(self) -> bool:

        return any(
            result.success
            and (result.document_type or "").strip().lower() == "business_case"
            for result in self.file_results
        )


# ======================================================================
# ATTACHMENT SERVICE
# ======================================================================

# Development mode: tampilkan SEMUA stage yang ditemukan (tidak difilter
# ke stage terbesar saja). Set True untuk perilaku production sebenarnya
# (hanya ambil attachment dari stage terbesar).
ENABLE_STAGE_FILTER = False


class AttachmentService:
    """
    Production Attachment Service

    Responsibilities
    ----------------
    1. Mencari attachment berdasarkan IdeaCode
       (format baru : IMS-{IdeaCode}_S{Stage}_{Sequence}.{Extension}
        format lama  : IMS-{IdeaCode}-{Sequence}.{Extension})
    2. Memilih Stage terbesar apabila ada beberapa stage (production mode)
    3. Mengurutkan attachment berdasarkan nomor urut
    4. Memanggil Dispatcher untuk tiap attachment
    5. Menggabungkan hasil ekstraksi menjadi AttachmentText
    6. Mencatat warning per kategori tanpa pernah menghentikan proses
    7. Menghasilkan AttachmentResult lengkap untuk IndexBuilder
    """

    # Format baru: IMS-004837_S2_001.pptx -> stage & order eksplisit di nama file
    STAGE_PATTERN = re.compile(r"^IMS-(\d+)_S(\d)_([0-9]{3}).*", re.IGNORECASE)

    # Format lama: IMS-004837-432531.pdf -> tidak ada penanda stage eksplisit
    LEGACY_PATTERN = re.compile(r"^IMS-(\d+)(?:[-_](.*))?$", re.IGNORECASE)

    def __init__(
        self,
        dispatcher: Optional[AttachmentDispatcher] = None,
        document_validator: Optional[Any] = None,
    ):

        self.root = Path(ATTACHMENT_ROOT)

        # Dependency injection -> memudahkan unit test (bisa inject mock dispatcher)
        self.dispatcher = dispatcher or AttachmentDispatcher()

        # Optional dependency keeps the existing attachment-indexing flow
        # unchanged. DocumentValidationService enables it explicitly.
        self.document_validator = document_validator

    # ==================================================================
    # FILENAME PARSING
    # ==================================================================

    def _parse_filename(self, file: Path) -> Optional[AttachmentFileInfo]:

        match = self.STAGE_PATTERN.match(file.stem)

        if match:

            return AttachmentFileInfo(
                idea_code=match.group(1),
                stage=int(match.group(2)),
                order=int(match.group(3)),
                path=file,
            )

        # ------------------------------------------------------------
        # Fallback: file tanpa penanda stage eksplisit (format lama),
        # contoh nyata: IMS-004837-432531.pdf
        # stage diset -1 sebagai penanda "belum ada info stage di nama
        # file", order diambil dari angka setelah idea_code supaya
        # urutan tetap konsisten/deterministik.
        # ------------------------------------------------------------

        legacy = self.LEGACY_PATTERN.match(file.stem)

        if legacy:

            suffix = legacy.group(2) or ""

            # Jika suffix dimulai dengan angka,
            # gunakan angka tersebut sebagai order.
            #
            # Contoh:
            # IMS-004431-630395.pptx
            # → order = 630395
            #
            # Jika suffix berupa nama:
            # IMS-006863-Fuel Dashboard Mockup.pptx
            # → order = 0

            order_match = re.match(r"(\d+)", suffix)

            order = int(order_match.group(1)) if order_match else 0

            return AttachmentFileInfo(
                idea_code=legacy.group(1), stage=-1, order=order, path=file
            )

    # ==================================================================
    # FIND FILES
    # ==================================================================

    def find_files(self, idea_code: str) -> List[AttachmentFileInfo]:

        if not self.root.exists():

            logger.warning(f"Attachment root tidak ditemukan: {self.root}")

            return []

        prefix = f"IMS-{idea_code}"

        candidates: List[AttachmentFileInfo] = []

        for file in self.root.iterdir():

            if not file.is_file():
                continue

            if not file.name.upper().startswith(prefix.upper()):
                continue

            parsed = self._parse_filename(file)

            if parsed is None:
                continue

            if parsed.idea_code != idea_code:
                continue

            candidates.append(parsed)

        if not candidates:
            return []

        # ===============================
        # DEVELOPMENT MODE
        # ===============================
        if not ENABLE_STAGE_FILTER:

            candidates.sort(key=lambda item: (item.stage, item.order))

            self._print_candidates("RETURN DEVELOPMENT", candidates)

            return candidates

        # ===============================
        # PRODUCTION MODE
        # ===============================

        max_stage = max(item.stage for item in candidates)

        selected = [item for item in candidates if item.stage == max_stage]

        selected.sort(key=lambda item: item.order)

        self._print_candidates("RETURN PRODUCTION", selected)

        return selected

    # ==================================================================
    # DEBUG PRINT HELPER (dipanggil sekali saja per find_files())
    # ==================================================================

    @staticmethod
    def _print_candidates(title: str, items: List[AttachmentFileInfo]) -> None:

        print("=" * 70)
        print(title)
        print("=" * 70)

        for item in items:
            print(f"- {item.path.name} (stage={item.stage})")

    # ==================================================================
    # SINGLE FILE EXTRACTION -> tidak pernah raise, selalu return result
    # ==================================================================

    def _extract_single_file(self, info: AttachmentFileInfo) -> AttachmentFileResult:

        file_name = info.file_name

        base_kwargs = dict(
            file_name=file_name,
            file_path=str(info.path),
            stage=info.stage,
            order=info.order,
        )

        if self.document_validator is not None:

            supports_extension = getattr(
                self.document_validator,
                "supports_extension",
                None,
            )

            if callable(supports_extension) and not supports_extension(
                info.path.suffix
            ):

                logger.info(
                    "[DOCUMENT VALIDATION SKIPPED] %s -> unsupported extension %s",
                    file_name,
                    info.path.suffix,
                )

                return AttachmentFileResult(
                    **base_kwargs,
                    success=False,
                    warning=AttachmentWarning.DOCUMENT_VALIDATION_UNSUPPORTED_FILE,
                )

        # --------------------------------------------------------
        # Panggil dispatcher -> tangkap semua kegagalan spesifik
        # --------------------------------------------------------

        try:

            result = self.dispatcher.dispatch(info.path)

        except FileNotFoundError:

            logger.warning(f"[CORRUPTED/MISSING] {file_name}")

            return AttachmentFileResult(
                **base_kwargs, success=False, warning=AttachmentWarning.CORRUPTED_FILE
            )

        except NotImplementedError:

            logger.warning(f"[UNSUPPORTED] {file_name}")

            return AttachmentFileResult(
                **base_kwargs, success=False, warning=AttachmentWarning.UNSUPPORTED_FILE
            )

        except Exception as ex:

            logger.error(f"[DISPATCHER FAILED] {file_name} -> {ex}")

            return AttachmentFileResult(
                **base_kwargs,
                success=False,
                warning=AttachmentWarning.DISPATCHER_FAILED,
            )

        # --------------------------------------------------------
        # Dispatcher berhasil dipanggil -> validasi hasilnya
        # --------------------------------------------------------

        result = result or {}

        text = str(result.get("text", "")).strip()
        logger.warning(
            "[EXTRACTION DEBUG] file=%s | text_length=%d | preview=%r",
            file_name,
            len(text),
            text[:500],
        )

        ocr_failed = bool(result.get("ocr_failed", False))

        if ocr_failed:

            logger.warning(f"[OCR FAILED] {file_name}")

            return AttachmentFileResult(
                **base_kwargs,
                success=False,
                text=text,
                warning=AttachmentWarning.OCR_FAILED,
            )

        if not text:

            logger.warning(f"[EMPTY EXTRACTION] {file_name}")

            return AttachmentFileResult(
                **base_kwargs, success=False, warning=AttachmentWarning.EMPTY_EXTRACTION
            )

        if self.document_validator is not None:

            validation = self.document_validator.validate(text)

            logger.warning(
                "[DOCUMENT VALIDATION DEBUG] "
                "file=%s | type=%s | supported=%s | matched=%s/%s | percentage=%.2f%% | reason=%s",
                file_name,
                validation.document_type.value,
                validation.is_supported,
                validation.matched_count,
                validation.total_markers,
                validation.match_percentage * 100,
                validation.reason,
            )

            logger.warning(
                "[DOCUMENT VALIDATION UNMATCHED] file=%s | unmatched=%s",
                file_name,
                validation.unmatched_markers,
            )

            if not validation.is_supported:

                logger.info(
                    "[DOCUMENT VALIDATION SKIPPED] %s -> %s",
                    file_name,
                    validation.document_type.value,
                )

                return AttachmentFileResult(
                    **base_kwargs,
                    success=False,
                    warning=AttachmentWarning.DOCUMENT_TYPE_NOT_ALLOWED,
                    document_type=validation.document_type.value,
                    match_percentage=validation.match_percentage,
                    matched_count=validation.matched_count,
                    total_markers=validation.total_markers,
                    reason=validation.reason,
                )

        return AttachmentFileResult(
            **base_kwargs,
            success=True,
            text=text,
            document_type=(
                validation.document_type.value
                if self.document_validator is not None
                else None
            ),
            match_percentage=(
                validation.match_percentage
                if self.document_validator is not None
                else 0.0
            ),
            matched_count=(
                validation.matched_count if self.document_validator is not None else 0
            ),
            total_markers=(
                validation.total_markers if self.document_validator is not None else 0
            ),
            reason=(validation.reason if self.document_validator is not None else None),
        )

    # ==================================================================
    # MAIN ENTRY POINT
    # ==================================================================

    def process(self, idea_code: str) -> AttachmentResult:
        """
        Entry point utama yang dipakai IndexBuilder.
        Selalu mengembalikan AttachmentResult, tidak pernah raise,
        tidak pernah menghentikan proses indexing idea lain.
        """

        files = self.find_files(idea_code)

        if not files:

            logger.warning(f"[ATTACHMENT MISSING] {idea_code}")

            return AttachmentResult(
                idea_code=idea_code,
                attachment_found=False,
                selected_stage=None,
                attachment_count=0,
                warnings=[AttachmentWarning.ATTACHMENT_MISSING],
            )

        selected_stage = files[0].stage

        file_results: List[AttachmentFileResult] = [
            self._extract_single_file(info) for info in files
        ]

        return self._build_result(idea_code, selected_stage, file_results)

    # ==================================================================
    # AGGREGATION
    # ==================================================================

    def _build_result(
        self,
        idea_code: str,
        selected_stage: int,
        file_results: List[AttachmentFileResult],
    ) -> AttachmentResult:

        texts = [fr.text for fr in file_results if fr.success]

        warnings = [fr.warning for fr in file_results if fr.warning]

        ocr_warnings = [
            fr.file_name
            for fr in file_results
            if fr.warning == AttachmentWarning.OCR_FAILED
        ]

        extraction_success = sum(1 for fr in file_results if fr.success)

        extraction_failed = len(file_results) - extraction_success

        return AttachmentResult(
            idea_code=idea_code,
            attachment_found=True,
            selected_stage=selected_stage,
            attachment_count=len(file_results),
            attachment_files=[fr.file_name for fr in file_results],
            attachment_text="\n\n".join(texts),
            warnings=warnings,
            ocr_warnings=ocr_warnings,
            extraction_success=extraction_success,
            extraction_failed=extraction_failed,
            file_results=file_results,
        )

    # ==================================================================
    # BACKWARD-COMPATIBLE HELPERS
    # (dipertahankan supaya code lain yang masih memanggil method lama
    #  tidak langsung rusak; secara internal semuanya delegasi ke process())
    # ==================================================================

    def get_attachment_text(self, idea_code: str) -> str:

        return self.process(idea_code).attachment_text

    def get_attachment_count(self, idea_code: str) -> int:

        return len(self.find_files(idea_code))

    def get_stage(self, idea_code: str) -> Optional[int]:

        files = self.find_files(idea_code)

        if not files:
            return None

        return files[0].stage

    def has_attachment(self, idea_code: str) -> bool:

        return self.get_attachment_count(idea_code) > 0
