from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.api.schemas import (
    SubmitSimilarityRequest,
    SubmitSimilarityResponse,
)

from app.services.submit_similarity import (
    SubmitSimilarityService,
    InvalidIdeaNameError,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/ideas/similarity",
    tags=["submit-similarity"],
)

_service = SubmitSimilarityService()

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
