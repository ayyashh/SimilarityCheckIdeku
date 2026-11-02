"""Compatibility re-exports for the document validation API service."""

from app.services.document_validation import (
    DocumentValidationService,
    InvalidDocumentError,
)

__all__ = ["DocumentValidationService", "InvalidDocumentError"]
