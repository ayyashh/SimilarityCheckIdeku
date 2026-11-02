"""Extract structured PDF content and compare it with document templates."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import pymupdf


class InvalidDocumentError(ValueError):
    """Raised when an uploaded PDF cannot be read or contains no text."""


@dataclass
class ExtractedPDFTable:
    page_number: int
    rows: List[List[Optional[str]]] = field(default_factory=list)


@dataclass
class ExtractedPDFPage:
    page_number: int
    text: str
    header_text: str
    header_present: Optional[bool]
    footer_text: str
    footer_ocr_used: bool = False
    ocr_warning: Optional[str] = None


@dataclass
class PDFExtraction:
    text: str
    pages: List[ExtractedPDFPage]
    tables: List[ExtractedPDFTable]


@dataclass
class TemplateTableValidation:
    is_valid: bool
    matched_tables: List[str]
    missing_tables: List[str]


@dataclass
class TemplateHeaderValidation:
    is_valid: bool
    required_text: str
    checked_pages: List[int]
    missing_pages: List[int]


class DocumentValidationService:
    MAX_FILE_SIZE = 20 * 1024 * 1024
    HEADER_REGION_END = 0.18
    REQUIRED_HEADER = "BUSINESS PERFORMANCE IMPROVEMENT MSE 3.06"
    FOOTER_REGION_START = 0.75
    TABLE_TEMPLATE_PATH = (
        Path(__file__).parents[1] / "document-validation" / "tables.json"
    )

    def __init__(self, validator: Any = None) -> None:
        if validator is None:
            validator_path = (
                Path(__file__).parents[1]
                / "document-validation"
                / "document_validator.py"
            )
            spec = importlib.util.spec_from_file_location(
                "app_document_validator", validator_path
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Document validator module could not be loaded.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            validator = module.DocumentValidator()
        self.validator = validator

    @classmethod
    def extract_pdf(cls, content: bytes) -> PDFExtraction:
        if not content:
            raise InvalidDocumentError("File PDF kosong.")
        if len(content) > cls.MAX_FILE_SIZE:
            raise InvalidDocumentError("Ukuran PDF melebihi batas 20 MB.")

        try:
            document = pymupdf.open(stream=content, filetype="pdf")
            if document.needs_pass and not document.authenticate(""):
                raise InvalidDocumentError("PDF terenkripsi dan tidak dapat dibaca.")

            pages: List[ExtractedPDFPage] = []
            tables: List[ExtractedPDFTable] = []
            for page_number, page in enumerate(document, start=1):
                page_text = page.get_text("text", sort=True).strip()
                header_bottom = page.rect.height * cls.HEADER_REGION_END
                header_blocks = [
                    block[4].strip()
                    for block in page.get_text("blocks", sort=True)
                    if len(block) >= 7
                    and block[6] == 0
                    and block[3] <= header_bottom
                    and block[4].strip()
                ]
                header_text = "\n".join(header_blocks)
                header_present = (
                    None
                    if page_number == 1
                    else cls._normalize_header(cls.REQUIRED_HEADER)
                    in cls._normalize_header(header_text)
                )
                footer_top = page.rect.height * cls.FOOTER_REGION_START
                footer_blocks = [
                    block[4].strip()
                    for block in page.get_text("blocks", sort=True)
                    if len(block) >= 7
                    and block[6] == 0
                    and block[1] >= footer_top
                    and block[4].strip()
                ]
                footer_ocr_used = False
                ocr_warning = None
                if not footer_blocks:
                    try:
                        textpage = page.get_textpage_ocr(
                            language="eng+ind",
                            dpi=200,
                            full=False,
                        )
                        footer_blocks = [
                            block[4].strip()
                            for block in page.get_text(
                                "blocks", textpage=textpage, sort=True
                            )
                            if len(block) >= 7
                            and block[6] == 0
                            and block[1] >= footer_top
                            and block[4].strip()
                        ]
                        footer_ocr_used = bool(footer_blocks)
                    except Exception:
                        ocr_warning = (
                            "Footer tidak terbaca sebagai teks PDF dan OCR tidak "
                            "tersedia. Pasang Tesseract beserta data bahasa eng dan ind."
                        )
                footer_text = "\n".join(footer_blocks)
                pages.append(
                    ExtractedPDFPage(
                        page_number=page_number,
                        text=page_text,
                        header_text=header_text,
                        header_present=header_present,
                        footer_text=footer_text,
                        footer_ocr_used=footer_ocr_used,
                        ocr_warning=ocr_warning,
                    )
                )

                table_finder = page.find_tables()
                for table in table_finder.tables:
                    rows = [
                        [None if cell is None else str(cell).strip() for cell in row]
                        for row in table.extract()
                    ]
                    if any(any(cell for cell in row) for row in rows):
                        tables.append(
                            ExtractedPDFTable(page_number=page_number, rows=rows)
                        )

            document.close()
        except InvalidDocumentError:
            raise
        except Exception as ex:
            raise InvalidDocumentError("File tidak dapat dibaca sebagai PDF.") from ex

        text = "\n\n".join(page.text for page in pages if page.text)
        if not text.strip():
            raise InvalidDocumentError(
                "PDF tidak berisi teks yang dapat dibaca; OCR belum tersedia."
            )
        return PDFExtraction(text=text, pages=pages, tables=tables)

    def validate_pdf(self, content: bytes):
        extraction = self.extract_pdf(content)
        result = self.validator.validate(extraction.text)
        table_validation = None
        header_validation = None
        if result.document_type.value == "handover":
            header_validation = self._validate_template_header(extraction)
            if not header_validation.is_valid:
                result.is_supported = False
                result.reason += (
                    " Header halaman wajib tidak ditemukan pada halaman: "
                    + ", ".join(map(str, header_validation.missing_pages))
                    + "."
                )
            table_validation = self._validate_template_tables(extraction)
            if not table_validation.is_valid:
                result.is_supported = False
                result.reason += (
                    " Header tabel template belum lengkap: "
                    + ", ".join(table_validation.missing_tables)
                    + "."
                )
        return result, extraction, table_validation, header_validation

    @classmethod
    def _validate_template_header(
        cls, extraction: PDFExtraction
    ) -> TemplateHeaderValidation:
        checked_pages = [page.page_number for page in extraction.pages if page.page_number > 1]
        missing_pages = [
            page.page_number
            for page in extraction.pages
            if page.page_number > 1 and page.header_present is not True
        ]
        return TemplateHeaderValidation(
            is_valid=not missing_pages,
            required_text=cls.REQUIRED_HEADER,
            checked_pages=checked_pages,
            missing_pages=missing_pages,
        )

    @staticmethod
    def _normalize_header(value: Any) -> str:
        normalized = str(value or "").lower()
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    @classmethod
    def _template_table_requirements(cls) -> List[tuple[str, List[str]]]:
        try:
            template_tables = json.loads(cls.TABLE_TEMPLATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as ex:
            raise RuntimeError("File acuan tables.json tidak dapat dibaca.") from ex

        requirements = []
        for table in template_tables:
            rows = table.get("rows", [])
            if not rows:
                continue
            first_row = [cls._normalize_header(cell) for cell in rows[0]]

            if any("kpi proyek" in cell for cell in first_row):
                name, header_rows = "Project KPI", rows[:1]
            elif any("outstanding tasks" in cell for cell in first_row):
                name, header_rows = "Outstanding Tasks", rows[:2]
            elif any("routine tasks" in cell for cell in first_row):
                name, header_rows = "Routine Tasks", rows[:2]
            elif any(
                "agreed by" in cls._normalize_header(cell)
                for row in rows
                for cell in row
            ):
                name = "Agreed by"
                header_rows = [
                    row
                    for row in rows
                    if any(
                        "agreed by" in cls._normalize_header(cell) for cell in row
                    )
                ][:1]
            elif any("document title" in cell for cell in first_row):
                name, header_rows = "Document control footer", rows[:1]
                if len(rows) > 2:
                    header_rows = [rows[0], rows[2]]
            else:
                continue

            headers = [
                cls._normalize_header(cell)
                for row in header_rows
                for cell in row
                if cls._normalize_header(cell)
            ]
            if headers:
                requirements.append((name, headers))
        return requirements

    @classmethod
    def _validate_template_tables(
        cls, extraction: PDFExtraction
    ) -> TemplateTableValidation:
        requirements = cls._template_table_requirements()
        candidate_tables = [
            (
                table.page_number,
                " ".join(
                    cls._normalize_header(cell)
                    for row in table.rows[:4]
                    for cell in row
                    if cell
                ),
            )
            for table in extraction.tables
        ]

        matched_tables = []
        missing_tables = []
        for table_name, headers in requirements:
            if table_name == "Document control footer":
                relevant_tables = [
                    text for page_number, text in candidate_tables if page_number > 1
                ]
                relevant_text = " ".join(
                    page.text for page in extraction.pages if page.page_number > 1
                )
            else:
                relevant_tables = [text for _, text in candidate_tables]
                relevant_text = extraction.text

            document_text = cls._normalize_header(relevant_text)
            document_text_compact = document_text.replace(" ", "")
            relevant_tables_compact = [
                table.replace(" ", "") for table in relevant_tables
            ]
            found_in_table = any(
                all(
                    header in candidate
                    or header.replace(" ", "") in candidate_compact
                    for header in headers
                )
                for candidate, candidate_compact in zip(
                    relevant_tables, relevant_tables_compact
                )
            )
            found_in_text = all(
                header in document_text
                or header.replace(" ", "") in document_text_compact
                for header in headers
            )
            if found_in_table or found_in_text:
                matched_tables.append(table_name)
            else:
                missing_tables.append(table_name)

        return TemplateTableValidation(
            is_valid=bool(requirements) and not missing_tables,
            matched_tables=matched_tables,
            missing_tables=missing_tables,
        )
