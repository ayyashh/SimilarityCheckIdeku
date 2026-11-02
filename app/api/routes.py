from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.schemas import (
    DocumentValidationFileResult,
    DocumentValidationHeaderCheck,
    DocumentValidationPage,
    DocumentValidationResponse,
    DocumentValidationTable,
    DocumentValidationTableCheck,
    SubmitSimilarityRequest,
    SubmitSimilarityResponse,
)

from app.services.submit_similarity import (
    SubmitSimilarityService,
    InvalidIdeaNameError,
)
from app.services.document_validation import (
    DocumentValidationService,
    InvalidDocumentError,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/ideas/similarity",
    tags=["submit-similarity"],
)
document_validation_router = APIRouter(
    prefix="/api/documents",
    tags=["document-validation"],
)

_service = SubmitSimilarityService()
_document_validation_service = DocumentValidationService()


@document_validation_router.post(
    "/validate",
    response_model=DocumentValidationResponse,
    tags=["document-validation"],
)
def validate_documents(
    file: UploadFile = File(...),
) -> DocumentValidationResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File yang diunggah harus PDF.")

    try:
        content = file.file.read(DocumentValidationService.MAX_FILE_SIZE + 1)
        result, extraction, table_validation, header_validation = (
            _document_validation_service.validate_pdf(content)
        )
    except InvalidDocumentError as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    except Exception:
        logger.exception("[DOCUMENT VALIDATION API] Unexpected error")
        raise HTTPException(status_code=500, detail="Internal server error")

    return DocumentValidationResponse(
        result=DocumentValidationFileResult(
            fileName=file.filename,
            isValid=result.is_supported,
            documentType=result.document_type.value,
            matchPercentage=round(result.match_percentage * 100, 2),
            matchedCount=result.matched_count,
            totalMarkers=result.total_markers,
            reason=result.reason,
            matchedMarkers=result.matched_markers,
            unmatchedMarkers=result.unmatched_markers,
        ),
        pages=[
            DocumentValidationPage(
                pageNumber=page.page_number,
                headerText=page.header_text,
                headerPresent=page.header_present,
                footerText=page.footer_text,
                footerOcrUsed=page.footer_ocr_used,
                ocrWarning=page.ocr_warning,
            )
            for page in extraction.pages
        ],
        tables=[
            DocumentValidationTable(
                pageNumber=table.page_number,
                rows=table.rows,
            )
            for table in extraction.tables
        ],
        tableValidation=(
            DocumentValidationTableCheck(
                isValid=table_validation.is_valid,
                matchedTables=table_validation.matched_tables,
                missingTables=table_validation.missing_tables,
            )
            if table_validation is not None
            else None
        ),
        headerValidation=(
            DocumentValidationHeaderCheck(
                isValid=header_validation.is_valid,
                requiredText=header_validation.required_text,
                checkedPages=header_validation.checked_pages,
                missingPages=header_validation.missing_pages,
            )
            if header_validation is not None
            else None
        ),
    )

@router.post(
    "/check",
    response_model=SubmitSimilarityResponse,
)
def check_submit_similarity(
    payload: SubmitSimilarityRequest,
) -> SubmitSimilarityResponse:

    try:

        result = _service.check_similarity(payload.ideaName, provider="gemini")

    except InvalidIdeaNameError as ex:

        raise HTTPException(
            status_code=400,
            detail=str(ex),
        )

    except Exception:

        logger.exception(
            "[SUBMIT SIMILARITY API] Unexpected error"
        )

        raise HTTPException(
            status_code=500,
            detail="Internal server error",
        )

    return _build_response(result)


@router.post(
    "/check/groq",
    response_model=SubmitSimilarityResponse,
)
def check_submit_similarity_groq(
    payload: SubmitSimilarityRequest,
) -> SubmitSimilarityResponse:
    try:
        result = _service.check_similarity(payload.ideaName, provider="groq")
    except InvalidIdeaNameError as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    except Exception:
        logger.exception("[SUBMIT SIMILARITY GROQ API] Unexpected error")
        raise HTTPException(status_code=500, detail="Internal server error")

    return _build_response(result)


@router.post(
    "/check/groq-120b",
    response_model=SubmitSimilarityResponse,
)
def check_submit_similarity_groq_120b(
    payload: SubmitSimilarityRequest,
) -> SubmitSimilarityResponse:
    try:
        result = _service.check_similarity(payload.ideaName, provider="groq_120b")
    except InvalidIdeaNameError as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    except Exception:
        logger.exception("[SUBMIT SIMILARITY GROQ 120B API] Unexpected error")
        raise HTTPException(status_code=500, detail="Internal server error")

    return _build_response(result)


@router.post(
    "/check/groq-qwen-27b",
    response_model=SubmitSimilarityResponse,
)
def check_submit_similarity_groq_qwen_27b(
    payload: SubmitSimilarityRequest,
) -> SubmitSimilarityResponse:
    try:
        result = _service.check_similarity(payload.ideaName, provider="groq_qwen_27b")
    except InvalidIdeaNameError as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    except Exception:
        logger.exception("[SUBMIT SIMILARITY GROQ QWEN 27B API] Unexpected error")
        raise HTTPException(status_code=500, detail="Internal server error")

    return _build_response(result)


def _build_response(result) -> SubmitSimilarityResponse:
    return SubmitSimilarityResponse(
        found=result.found,
        threshold=result.threshold,
        topK=result.top_k,
        evaluationMethod=result.evaluation_method,
        returnedCount=result.returned_count,
        results=[
            {
                "ideaId": candidate.idea_id,
                "ideaName": candidate.idea_name,
                "description": candidate.description,
                "ideaSolution": candidate.idea_solution,
                "toDivision": candidate.to_division,
                "similarityPercentage": candidate.similarity_percentage,
                "cosineSimilarityPercentage": candidate.cosine_similarity_percentage,
                "aiVerdict": candidate.ai_verdict,
                "isOpposite": candidate.is_opposite,
                "reason": {
                    "summary": candidate.reason.summary,
                    "matchedFields": candidate.reason.matched_fields,
                    "highlights": candidate.reason.highlights,
                    "evidence": [
                        {
                            "highlight": evidence.highlight,
                            "field": evidence.field,
                            "queryText": evidence.query_text,
                            "candidateText": evidence.candidate_text,
                        }
                        for evidence in candidate.reason.evidence
                    ],
                },
                "difference": {
                    "queryFocus": candidate.difference.query_focus,
                    "candidateFocus": candidate.difference.candidate_focus,
                    "differences": candidate.difference.differences,
                    "sharedPoints": candidate.difference.shared_points,
                },
            }
            for candidate in result.results
        ],
        message=result.message,
    )
