from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class SubmitSimilarityRequest(BaseModel):
    ideaName: str


class SubmitSimilarityResponse(BaseModel):
    found: bool
    threshold: float
    topK: int
    evaluationMethod: str
    results: List[Dict[str, Any]]
    returnedCount: int = 0
    message: Optional[str] = None
