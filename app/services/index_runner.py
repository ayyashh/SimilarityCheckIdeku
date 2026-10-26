from __future__ import annotations

import logging
import time

from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Sequence,
)

from app.repositories.ideas import (
    IdeaRepository,
)

from app.services.index_builder import (
    IndexBuilder,
    IndexBuildResult,
)

from app.services.attachment_service import AttachmentService
from documentvalidation.document_validator import DocumentValidator

from app.services.embedding import (
    EmbeddingService,
)

from app.repository.semantic_index_repository import (
    SemanticIndexRepository,
    SemanticIndexRecord,
)

from app.repository.document_validation_repository import (
    DocumentValidationRepository,
    DocumentValidationRecord,
)

from app.services.indexing_history_service import (
    IndexingHistoryService,
)

from app.services.document_validation_history_service import (
    DocumentValidationHistoryService,
)

logger = logging.getLogger(__name__)


# ======================================================================
# RUN STATISTICS
# ======================================================================


@dataclass
class IndexRunStats:

    total: int = 0
    inserted: int = 0
    updated: int = 0
    failed: int = 0
    skipped: int = 0

    file_results: List[Any] = field(default_factory=list)

    attachment_found: int = 0
    attachment_missing: int = 0

    idea_only: int = 0
    idea_attachment: int = 0

    ocr_warning: int = 0

    files_found: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    handover_documents: int = 0
    business_case_documents: int = 0

    total_time: float = 0.0

    @property
    def success(self) -> int:

        return self.inserted + self.updated

    @property
    def average_time(self) -> float:

        if self.total == 0:

            return 0.0

        return self.total_time / self.total

    def to_dict(self) -> Dict[str, Any]:

        return {
            "Total": self.total,
            "Inserted": self.inserted,
            "Updated": self.updated,
            "Failed": self.failed,
            "Skipped": self.skipped,
            "AttachmentFound": self.attachment_found,
            "AttachmentMissing": self.attachment_missing,
            "IdeaOnly": self.idea_only,
            "IdeaAttachment": self.idea_attachment,
            "OCRWarning": self.ocr_warning,
            "FilesFound": self.files_found,
            "FilesProcessed": self.files_processed,
            "FilesSkipped": self.files_skipped,
            "HandoverDocuments": self.handover_documents,
            "BusinessCaseDocuments": self.business_case_documents,
            "TotalTime": self.total_time,
            "AverageTime": self.average_time,
        }


@dataclass
class DocumentValidationRunStats:

    total_ideas: int = 0
    processed_ideas: int = 0
    failed_ideas: int = 0
    skipped_ideas: int = 0

    total_documents: int = 0
    processed_documents: int = 0
    skipped_documents: int = 0

    valid_documents: int = 0
    invalid_documents: int = 0


# ======================================================================
# INDEX RUNNER
# ======================================================================


class IndexRunner:
    """
    Pipeline:

    IndexBuilder
        ->
    EmbeddingService
        ->
    SemanticIndexRepository

    Scheduled indexing menggunakan incremental indexing.

    Full indexing tetap tersedia untuk kebutuhan manual.
    """

    def __init__(
        self,
        idea_repository: Optional[IdeaRepository] = None,
        builder: Optional[IndexBuilder] = None,
        embedding_service: Optional[EmbeddingService] = None,
        semantic_repository: Optional[SemanticIndexRepository] = None,
        history_service: Optional[IndexingHistoryService] = None,
        document_validation_repository: Optional[DocumentValidationRepository] = None,
    ):

        self.idea_repository = idea_repository or IdeaRepository()

        self.builder = builder or IndexBuilder(
            attachment_service=AttachmentService(
                document_validator=DocumentValidator()
            )
        )

        self.embedding_service = embedding_service or EmbeddingService()

        self.semantic_repository = semantic_repository or SemanticIndexRepository()

        self.history_service = history_service or IndexingHistoryService()

        self.document_validation_repository = (
            document_validation_repository or DocumentValidationRepository()
        )

        self.document_validation_history_service = DocumentValidationHistoryService()

    # ==================================================================
    # ENTRY POINT
    # ==================================================================

    def run(
        self,
        limit: Optional[int] = None,
        run_type: Optional[str] = None,
        triggered_by: str = "manual",
    ) -> IndexRunStats:

        resolved_run_type = run_type or ("FULL" if limit is None else "MANUAL")

        # ==============================================================
        # SCHEDULED
        # ==============================================================
        #
        # Scheduler menggunakan incremental indexing.
        #
        # Hanya mengambil:
        #
        # - Idea baru
        # - Idea yang UpdatedDate-nya lebih baru dari LastUpdated index
        #
        # ==============================================================

        if resolved_run_type == "SCHEDULED":

            ideas = self.idea_repository.get_pending_for_indexing(limit=limit)

            logger.info("[INCREMENTAL INDEXING] " f"Pending Ideas={len(ideas)}")

        # ==============================================================
        # FULL / MANUAL
        # ==============================================================

        else:

            ideas = self._fetch_ideas(limit)

        idea_codes = [
            idea.get(
                "IdeaCode",
                "",
            )
            for idea in ideas
            if idea.get("IdeaCode")
        ]

        return self._run_pipeline(
            idea_codes=idea_codes,
            run_type=resolved_run_type,
            triggered_by=triggered_by,
        )

    # ==================================================================
    # TARGETED
    # ==================================================================

    def run_by_codes(
        self,
        idea_codes: Sequence[str],
        triggered_by: str = "manual",
    ) -> IndexRunStats:

        return self._run_pipeline(
            idea_codes=list(idea_codes),
            run_type="TARGETED",
            triggered_by=triggered_by,
        )

    # ==================================================================
    # FETCH IDEAS
    # ==================================================================

    def _fetch_ideas(
        self,
        limit: Optional[int],
    ) -> List[Dict[str, Any]]:

        if limit is not None:

            return self.idea_repository.get_top(limit)

        return self.idea_repository.get_all()

    # ==================================================================
    # SHARED PIPELINE
    # ==================================================================

    def _run_pipeline(
        self,
        idea_codes: List[str],
        run_type: str = "MANUAL",
        triggered_by: str = "manual",
    ) -> IndexRunStats:

        total = len(idea_codes)

        stats = IndexRunStats(total=total)

        validation_stats = DocumentValidationRunStats(
            total_ideas=total,
        )

        run_id = self.history_service.start_run(
            run_type=run_type,
            triggered_by=triggered_by,
            total_ideas=total,
        )

        validation_run_id = self.document_validation_history_service.start_run(
            total_ideas=total,
        )

        print("=" * 80)

        print(f"TOTAL IDEA : {total}")

        print("=" * 80)

        try:

            # ==========================================================
            # Tidak ada perubahan
            # ==========================================================

            if total == 0:

                print()
                print("[INCREMENTAL INDEXING] " "Tidak ada Idea yang perlu diproses.")

            # ==========================================================
            # PROCESS
            # ==========================================================

            for index, idea_code in enumerate(
                idea_codes,
                start=1,
            ):

                self._process_single_idea(
                    index=index,
                    total=total,
                    idea_code=idea_code,
                    stats=stats,
                    validation_stats=validation_stats,
                )

        except Exception as ex:

            logger.error(
                "[INDEX RUNNER] " f"Fatal error during batch execution -> {ex}"
            )

            self.history_service.fail_run(
                run_id,
                ex,
            )

            self.document_validation_history_service.fail_run(
                validation_run_id,
                ex,
            )

            raise

        self._print_summary(stats)

        self.history_service.complete_run(
            run_id,
            stats,
        )

        self.document_validation_history_service.complete_run(
            validation_run_id,
            validation_stats,
        )

        return stats

    # ==================================================================
    # PROCESS SINGLE IDEA
    # ==================================================================

    def _process_single_idea(
        self,
        index: int,
        total: int,
        idea_code: str,
        stats: IndexRunStats,
        validation_stats: DocumentValidationRunStats,
    ) -> None:

        start_time = time.time()

        print()
        print("-" * 80)

        print(f"[{index}/{total}] {idea_code}")

        # ==============================================================
        # STEP 1
        # BUILD
        # ==============================================================

        build_result = self._safe_build(idea_code)

        if build_result is None or not build_result.idea_found:

            stats.skipped += 1

            validation_stats.skipped_ideas += 1

            elapsed = time.time() - start_time

            stats.total_time += elapsed

            attachment_text_length = 0

            if build_result is not None:

                attachment_text_length = len(
                    (build_result.attachment_text or "").strip()
                )

            print(f"Attachment Text Length : " f"{attachment_text_length}")

            print("Result  : SKIPPED " "(Idea Not Found)")

            print(f"Elapsed : {elapsed:.2f} sec")

            return

        self._collect_build_stats(
            build_result,
            stats,
        )
        attachment_text_length = len((build_result.attachment_text or "").strip())

        # ==============================================================
        # DOCUMENT VALIDATION
        # ==============================================================

        self._collect_document_validation_stats(
            build_result,
            validation_stats,
        )

        self._safe_save_document_validation(
            build_result,
        )

        # ==============================================================
        # STEP 2
        # EMBEDDING
        # ==============================================================

        vector = self._safe_encode(build_result)

        if vector is None:

            stats.failed += 1

            elapsed = time.time() - start_time

            stats.total_time += elapsed

            print(f"Embedding Source : " f"{build_result.embedding_source}")
            print(f"Attachment       : " f"{build_result.attachment_count}")

            print(f"Attachment Text  : {attachment_text_length} characters")

            print("Result           : " "FAILED (Embedding Error)")

            print(f"Elapsed          : " f"{elapsed:.2f} sec")

            return

        # ==============================================================
        # STEP 3
        # SAVE / UPSERT
        # ==============================================================

        save_status = self._safe_save(
            build_result,
            vector,
        )

        elapsed = time.time() - start_time

        stats.total_time += elapsed

        print(f"Embedding Source : " f"{build_result.embedding_source}")

        print(f"Attachment       : " f"{build_result.attachment_count}")

        if save_status is None:

            stats.failed += 1

            print("Result           : " "FAILED (Save Error)")

        elif save_status == "INSERTED":

            stats.inserted += 1

            print("Result           : INSERTED")

        else:

            stats.updated += 1

            print("Result           : UPDATED")

        print(f"Elapsed          : " f"{elapsed:.2f} sec")

    

    def _safe_build(
        self,
        idea_code: str,
    ) -> Optional[IndexBuildResult]:

        try:

            return self.builder.build(idea_code)

        except Exception as ex:

            logger.error(f"[BUILD FAILED] " f"{idea_code} -> {ex}")

            return None

    # ==================================================================
    # SAFE ENCODE
    # ==================================================================

    def _safe_encode(
        self,
        build_result: IndexBuildResult,
    ) -> Optional[List[float]]:

        try:

            return self.embedding_service.encode(build_result.combined_text)

        except Exception as ex:

            logger.error(f"[EMBEDDING FAILED] " f"{build_result.idea_code} -> {ex}")

            return None

    # ==================================================================
    # SAFE SAVE
    # ==================================================================

    def _safe_save(
        self,
        build_result: IndexBuildResult,
        vector: List[float],
    ) -> Optional[str]:

        try:

            attachment_text = (build_result.attachment_text or "").strip()

            if build_result.attachment_found and not attachment_text:

                logger.warning(
                    f"[ATTACHMENT TEXT EMPTY] "
                    f"IdeaCode={build_result.idea_code} "
                    f"AttachmentCount={build_result.attachment_count} "
                    f"Files={build_result.attachment_files}"
                )
            record = SemanticIndexRecord(
                idea_id=build_result.idea_id,
                combined_text=build_result.combined_text,
                attachment_text=attachment_text,
                embedding=vector,
                embedding_dimension=self.embedding_service.dimension(),
                model_name=self.embedding_service.model_name(),
            )

            return self.semantic_repository.save(record)

        except Exception as ex:

            logger.error(f"[SAVE FAILED] " f"{build_result.idea_code} -> {ex}")

            return None

    # ==================================================================
    # DOCUMENT VALIDATION
    # ==================================================================

    def _safe_save_document_validation(
        self,
        build_result: IndexBuildResult,
    ) -> None:

        try:

            idea_id = build_result.idea_id

            if not idea_id:

                logger.warning(
                    f"[DOCUMENT VALIDATION] "
                    f"{build_result.idea_code} -> IdeaId tidak tersedia"
                )

                return

            validation_results = []

            for file_result in build_result.file_results:

                document_type = (file_result.document_type or "").strip().lower()

                if document_type not in (
                    "handover",
                    "business_case",
                ):
                    continue

                if document_type == "business_case":
                    is_valid = (
                        file_result.matched_count >= 4
                        or file_result.match_percentage >= 0.85
                    )
                else:
                    is_valid = file_result.match_percentage >= 0.85

                validation_results.append(
                    DocumentValidationRecord(
                        idea_id=idea_id,
                        document_type=document_type.upper(),
                        file_name=file_result.file_name,
                        status=("valid" if is_valid else "invalid"),
                        is_valid=is_valid,
                        match_percentage=file_result.match_percentage * 100,
                        matched_parameters=file_result.matched_count,
                        total_parameters=file_result.total_markers,
                        reason=file_result.reason,
                    )
                )

            if not validation_results:
                logger.info(
                    f"[DOCUMENT VALIDATION] "
                    f"{build_result.idea_code} -> "
                    f"Tidak ada dokumen Handover / Business Case"
                )

                return

            self.document_validation_repository.sync_for_idea(
                idea_id,
                validation_results,
            )

        except Exception as ex:
            logger.error(
                f"[DOCUMENT VALIDATION FAILED] " f"{build_result.idea_code} -> {ex}"
            )

            return

    def _collect_document_validation_stats(
        self,
        build_result: IndexBuildResult,
        validation_stats: DocumentValidationRunStats,
    ) -> None:

        validation_stats.processed_ideas += 1

        for file_result in build_result.file_results:

            document_type = (file_result.document_type or "").strip().lower()

            if document_type not in (
                "handover",
                "business_case",
            ):
                continue

            validation_stats.total_documents += 1

            if not file_result.success:
                validation_stats.skipped_documents += 1
                continue

            validation_stats.processed_documents += 1

            if document_type == "business_case":
                is_valid = (
                    file_result.matched_count >= 4
                    or file_result.match_percentage >= 0.85
                )
            else:
                is_valid = file_result.match_percentage >= 0.85

            if is_valid:
                validation_stats.valid_documents += 1
            else:
                validation_stats.invalid_documents += 1

    # ==================================================================
    # BUILD STATS

    def _collect_build_stats(
        self,
        build_result: IndexBuildResult,
        stats: IndexRunStats,
    ) -> None:

        if build_result.attachment_found:
            stats.attachment_found += 1

        else:
            stats.attachment_missing += 1

        if build_result.embedding_source == "IDEA_ATTACHMENT":
            stats.idea_attachment += 1

        else:
            stats.idea_only += 1

        if build_result.ocr_warnings:
            stats.ocr_warning += 1

        stats.files_found += build_result.attachment_count
        stats.files_processed += build_result.files_processed
        stats.files_skipped += build_result.files_skipped

        if build_result.handover_document_found:
            stats.handover_documents += 1

        if build_result.business_case_document_found:
            stats.business_case_documents += 1

        stats.file_results.extend(build_result.file_results)

    # ==================================================================
    # SUMMARY
    # ==================================================================

    def _print_summary(
        self,
        stats: IndexRunStats,
    ) -> None:

        print()

        print("=" * 80)

        print("FINISHED")

        print("=" * 80)
        print(f"Total              : {stats.total}")
        print(f"Inserted           : {stats.inserted}")
        print(f"Updated            : {stats.updated}")
        print(f"Failed             : {stats.failed}")
        print(f"Skipped            : {stats.skipped}")
        print(f"Attachment Missing : {stats.attachment_missing}")
        print(f"Idea Only          : {stats.idea_only}")
        print(f"Idea + Attachment  : {stats.idea_attachment}")
        print(f"OCR Warning        : {stats.ocr_warning}")
        print(f"Total Time         : {stats.total_time:.2f} sec")
        print(f"Average Time       : {stats.average_time:.2f} sec")
        print("=" * 80)
