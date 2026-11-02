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


class DocumentValidationFileResult(BaseModel):
    fileName: str
    isValid: bool
    documentType: Optional[str] = None
    matchPercentage: float = 0.0
    matchedCount: int = 0
    totalMarkers: int = 0
    reason: Optional[str] = None
    matchedMarkers: List[str]
    unmatchedMarkers: List[str]


class DocumentValidationPage(BaseModel):
    pageNumber: int
    headerText: str
    headerPresent: Optional[bool] = None
    footerText: str
    footerOcrUsed: bool = False
    ocrWarning: Optional[str] = None


class DocumentValidationTable(BaseModel):
    pageNumber: int
    rows: List[List[Optional[str]]]


class DocumentValidationTableCheck(BaseModel):
    isValid: bool
    matchedTables: List[str]
    missingTables: List[str]


class DocumentValidationHeaderCheck(BaseModel):
    isValid: bool
    requiredText: str
    checkedPages: List[int]
    missingPages: List[int]


class DocumentValidationResponse(BaseModel):
    result: DocumentValidationFileResult
    pages: List[DocumentValidationPage]
    tables: List[DocumentValidationTable]
    tableValidation: Optional[DocumentValidationTableCheck] = None
    headerValidation: Optional[DocumentValidationHeaderCheck] = None
