import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List


class DocumentType(Enum):
    HANDOVER = "handover"
    BUSINESS_CASE = "business_case"
    OTHER = "other"


@dataclass
class DocumentValidationResult:
    is_supported: bool
    document_type: DocumentType
    reason: str
    matched_count: int
    total_markers: int
    match_percentage: float
    matched_markers: List[str]
    unmatched_markers: List[str]


class HandoverDocumentValidator:
    """Validate required Handover sections derived from the extracted template."""

    SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx"}
    MATCH_THRESHOLD = 1.0
    TEMPLATE_PATH = (
        Path(__file__).parent / "extracted.txt"
    )
    SECTION_PATTERN = re.compile(
        r"^\s*((?:I|II|III|IV|V)\.\s+.+?)\s*$", re.IGNORECASE
    )

    def __init__(self):
        if not self.TEMPLATE_PATH.is_file():
            raise FileNotFoundError(
                f"Handover template text not found: {self.TEMPLATE_PATH}"
            )
        template_text = self.TEMPLATE_PATH.read_text(encoding="utf-8-sig")
        self.template_sections = [
            match.group(1).strip()
            for line in template_text.splitlines()
            if (match := self.SECTION_PATTERN.match(line))
        ]
        if not self.template_sections:
            raise ValueError("No numbered Handover sections found in the template.")

    def supports_extension(self, extension: str) -> bool:
        return bool(extension) and extension.lower() in self.SUPPORTED_EXTENSIONS

    @staticmethod
    def _normalize(value: str) -> str:
        if not value:
            return ""
        value = value.lower().replace("&", " and ")
        value = value.replace("–", "-").replace("—", "-").replace("_", " ")
        value = re.sub(r"[^a-z0-9\s]", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def validate(self, text: str) -> DocumentValidationResult:
        if not text or not text.strip():
            return DocumentValidationResult(
                is_supported=False,
                document_type=DocumentType.OTHER,
                reason="Dokumen tidak memiliki teks yang dapat divalidasi.",
                matched_count=0,
                total_markers=len(self.template_sections),
                match_percentage=0.0,
                matched_markers=[],
                unmatched_markers=self.template_sections.copy(),
            )

        normalized_lines = [
            self._normalize(line)
            for line in text.splitlines()
            if self._normalize(line)
        ]
        matched_markers = []
        unmatched_markers = []
        section_positions = []

        for section in self.template_sections:
            normalized_section = self._normalize(section)
            try:
                position = normalized_lines.index(normalized_section)
            except ValueError:
                position = -1
            if position >= 0:
                matched_markers.append(section)
                section_positions.append(position)
            else:
                unmatched_markers.append(section)

        total_markers = len(self.template_sections)
        matched_count = len(matched_markers)
        match_percentage = matched_count / total_markers if total_markers else 0.0
        sections_in_order = section_positions == sorted(section_positions)
        is_supported = (
            match_percentage >= self.MATCH_THRESHOLD and sections_in_order
        )

        if is_supported:
            document_type = DocumentType.HANDOVER
            reason = (
                f"Struktur dokumen sesuai template Handover. "
                f"{matched_count}/{total_markers} bagian ditemukan "
                f"({match_percentage * 100:.2f}%)."
            )
        else:
            document_type = DocumentType.OTHER
            if not sections_in_order:
                reason = "Bagian dokumen ditemukan, tetapi urutannya tidak sesuai template Handover."
            else:
                reason = (
                    f"Struktur dokumen belum sesuai template Handover. "
                    f"{matched_count}/{total_markers} bagian ditemukan "
                    f"({match_percentage * 100:.2f}%), minimum "
                    f"{self.MATCH_THRESHOLD * 100:.0f}%."
                )

        return DocumentValidationResult(
            is_supported=is_supported,
            document_type=document_type,
            reason=reason,
            matched_count=matched_count,
            total_markers=total_markers,
            match_percentage=match_percentage,
            matched_markers=matched_markers,
            unmatched_markers=unmatched_markers,
        )

class BusinessCaseDocumentValidator:
    """
    Validator Business Case berbasis konten.

    Business Case tidak memiliki satu template yang seragam,
    sehingga validasi dilakukan berdasarkan 5 parameter inti:

    1. Problem / Current Situation       = 25%
    2. Objective / Target                = 15%
    3. Proposed Solution / Improvement   = 25%
    4. Benefit / Impact                  = 15%
    5. Implementation / Action Plan     = 20%

    Setiap parameter memiliki beberapa variasi istilah.
    Jika salah satu variasi ditemukan, parameter dianggap terpenuhi.
    """

    SUPPORTED_EXTENSIONS = {
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
    }

    MATCH_THRESHOLD = 0.85

    BUSINESS_CASE_PARAMETERS = [
        (
            "Problem / Current Situation",
            0.25,
            [
                "problem statement",
                "problem",
                "current situation",
                "current condition",
                "background",
                "latar belakang",
                "kondisi saat ini",
                "kondisi awal",
                "kondisi sebelum improvement",
                "permasalahan",
                "masalah",
            ],
        ),
        (
            "Objective / Target",
            0.15,
            [
                "objective",
                "objectives",
                "goal",
                "goals",
                "target",
                "tujuan",
                "sasaran",
                "aspiration",
                "aspirations",
                "purpose",
            ],
        ),
        (
            "Proposed Solution / Improvement",
            0.25,
            [
                "proposed solution",
                "project proposed",
                "project proposal",
                "solution",
                "improvement",
                "proposed improvement",
                "improvement area",
                "project solution",
                "solusi",
                "usulan solusi",
                "usulan perbaikan",
                "perbaikan",
                "inisiatif improvement",
                "detail inisiatif improvement",
            ],
        ),
        (
            "Benefit / Impact",
            0.15,
            [
                "benefit",
                "benefits",
                "benefit improvement",
                "benefit / improvement area",
                "improvement benefit",
                "business benefit",
                "business impact",
                "impact",
                "improvement impact",
                "securing",
                "saving",
                "manfaat",
                "manfaat improvement",
                "dampak",
                "dampak bisnis",
                "mengamankan",
            ],
        ),
        (
            "Implementation / Action Plan",
            0.20,
            [],
        ),
    ]

    @staticmethod
    def _normalize(value: str) -> str:
        """
        Normalisasi teks agar pencarian marker tidak sensitif terhadap:

        - huruf besar/kecil
        - tanda baca
        - line break
        - tab
        - spasi berlebih
        """

        if not value:
            return ""

        value = value.lower()

        # Samakan variasi ampersand
        value = value.replace("&", " and ")

        # Samakan variasi dash
        value = value.replace("–", "-")
        value = value.replace("—", "-")
        value = value.replace("_", " ")

        # Hilangkan tanda baca
        value = re.sub(r"[^a-z0-9\s]", " ", value)

        # Rapikan whitespace
        value = re.sub(r"\s+", " ", value)

        return value.strip()

    def _contains_marker(
        self,
        text: str,
        marker: str,
    ) -> bool:
        """
        Mengecek apakah marker ditemukan di dalam dokumen.
        """

        normalized_text = self._normalize(text)
        normalized_marker = self._normalize(marker)

        if not normalized_text or not normalized_marker:
            return False

        return normalized_marker in normalized_text

    def _contains_implementation_indicator(
        self,
        text: str,
    ) -> bool:
        """
        Mengecek apakah dokumen memiliki indikasi
        Implementation / Action Plan berdasarkan:

        - Implementation / Execution
        - Activity / Stage
        - Time / Schedule
        """

        implementation_variants = [
            "action plan",
            "implementation plan",
            "implementation",
            "project implementation",
            "implementation process",
            "implementation strategy",
            "execution plan",
            "execution",
            "project execution",
            "rencana aksi",
            "rencana implementasi",
            "rencana pelaksanaan",
        ]

        activity_variants = [
            "work plan",
            "workplan",
            "work program",
            "program activities",
            "plan activity",
            "activity plan",
            "project activities",
            "planned activities",
            "action items",
            "action item",
            "project phase",
            "project phases",
            "project stage",
            "project stages",
            "implementation phase",
            "implementation phases",
            "phase",
            "phases",
            "stage",
            "stages",
            "tahapan",
            "tahap",
            "kegiatan",
            "aktivitas",
            "rencana kerja",
            "rencana kegiatan",
            "tahapan pelaksanaan",
            "tahap pelaksanaan",
        ]

        time_variants = [
            "timeline",
            "time frame",
            "timeframe",
            "project timeline",
            "project schedule",
            "implementation timeline",
            "implementation schedule",
            "work schedule",
            "schedule",
            "target date",
            "target dates",
            "duration",
            "project duration",
            "when",
            "jadwal",
            "jadwal pelaksanaan",
            "target waktu",
            "jangka waktu",
            "durasi",
        ]

        all_variants = implementation_variants + activity_variants + time_variants

        return any(self._contains_marker(text, variant) for variant in all_variants)

    def supports_extension(self, extension: str) -> bool:
        """
        Mengecek apakah extension file dapat divalidasi.
        """

        if not extension:
            return False

        return extension.lower() in self.SUPPORTED_EXTENSIONS

    def validate(self, text: str) -> DocumentValidationResult:
        """
        Melakukan validasi Business Case menggunakan weighted scoring.
        """

        if not text or not text.strip():

            parameter_names = [
                parameter_name for parameter_name, _, _ in self.BUSINESS_CASE_PARAMETERS
            ]

            return DocumentValidationResult(
                is_supported=False,
                document_type=DocumentType.OTHER,
                reason="Dokumen tidak memiliki teks yang dapat divalidasi.",
                matched_count=0,
                total_markers=len(self.BUSINESS_CASE_PARAMETERS),
                match_percentage=0.0,
                matched_markers=[],
                unmatched_markers=parameter_names,
            )

        matched_markers = []
        unmatched_markers = []

        score = 0.0

        for parameter_name, weight, variants in self.BUSINESS_CASE_PARAMETERS:

            if parameter_name == "Implementation / Action Plan":
                parameter_matched = self._contains_implementation_indicator(text)

            else:
                parameter_matched = False

                for variant in variants:

                    if self._contains_marker(text, variant):
                        parameter_matched = True
                        break

            if parameter_matched:

                matched_markers.append(parameter_name)
                score += weight

            else:

                unmatched_markers.append(parameter_name)

        total_markers = len(self.BUSINESS_CASE_PARAMETERS)
        matched_count = len(matched_markers)

        match_percentage = score

        is_supported = matched_count >= 4 or match_percentage >= self.MATCH_THRESHOLD

        document_type = DocumentType.BUSINESS_CASE

        if is_supported:
            reason = (
                f"Dokumen teridentifikasi sebagai Business Case. "
                f"{matched_count}/{total_markers} parameter utama terpenuhi "
                f"({match_percentage * 100:.2f}%)."
            )
        else:
            reason = (
                f"Dokumen teridentifikasi sebagai Business Case, "
                f"tetapi belum memenuhi kriteria validasi. "
                f"{matched_count}/{total_markers} parameter utama terpenuhi "
                f"({match_percentage * 100:.2f}%), "
                f"minimum 4 parameter atau weighted score "
                f"{self.MATCH_THRESHOLD * 100:.0f}%."
            )

        return DocumentValidationResult(
            is_supported=is_supported,
            document_type=document_type,
            reason=reason,
            matched_count=matched_count,
            total_markers=total_markers,
            match_percentage=match_percentage,
            matched_markers=matched_markers,
            unmatched_markers=unmatched_markers,
        )


class DocumentValidator:
    """
    Validator gabungan untuk menentukan jenis dokumen.

    Validator akan mencoba:
    - Handover Document
    - Business Case

    Kemudian memilih hasil dengan score tertinggi.
    """

    SUPPORTED_EXTENSIONS = {
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
    }

    def __init__(self):
        self.handover_validator = HandoverDocumentValidator()
        self.business_case_validator = BusinessCaseDocumentValidator()

    def supports_extension(self, extension: str) -> bool:
        if not extension:
            return False

        return extension.lower() in self.SUPPORTED_EXTENSIONS

    def validate(self, text: str) -> DocumentValidationResult:

        if not text or not text.strip():

            return DocumentValidationResult(
                is_supported=False,
                document_type=DocumentType.OTHER,
                reason="Dokumen tidak memiliki teks yang dapat divalidasi.",
                matched_count=0,
                total_markers=0,
                match_percentage=0.0,
                matched_markers=[],
                unmatched_markers=[],
            )

        handover_result = self.handover_validator.validate(text)
        business_case_result = self.business_case_validator.validate(text)

        # --------------------------------------------------------
        # Jika Handover memenuhi threshold
        # --------------------------------------------------------

        if handover_result.match_percentage >= self.handover_validator.MATCH_THRESHOLD:
            return handover_result

        # --------------------------------------------------------
        # Jika Business Case memenuhi threshold
        # --------------------------------------------------------

        if business_case_result.is_supported:
            return business_case_result

        # --------------------------------------------------------
        # Jika keduanya tidak memenuhi threshold,
        # pilih score tertinggi
        # --------------------------------------------------------

        if business_case_result.match_percentage > handover_result.match_percentage:
            return business_case_result

        return handover_result
