"""Manually inspect extracted PDF text, footer blocks, and detected tables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.document_validation import (  # noqa: E402
    DocumentValidationService,
    InvalidDocumentError,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Periksa teks, footer, dan tabel hasil ekstraksi PDF."
    )
    parser.add_argument("pdf", type=Path, help="Path file PDF yang akan diperiksa")
    parser.add_argument(
        "--output", "-o", type=Path, help="Simpan seluruh teks ke file UTF-8"
    )
    parser.add_argument(
        "--tables-output",
        type=Path,
        help="Simpan tabel hasil ekstraksi ke file JSON",
    )
    args = parser.parse_args()

    try:
        extraction = DocumentValidationService.extract_pdf(args.pdf.read_bytes())
    except OSError as ex:
        print(f"Gagal membaca file: {ex}", file=sys.stderr)
        return 1
    except InvalidDocumentError as ex:
        print(f"Ekstraksi gagal: {ex}", file=sys.stderr)
        return 1

    print(f"File: {args.pdf}")
    print(f"Jumlah halaman: {len(extraction.pages)}")
    print(f"Jumlah karakter: {len(extraction.text)}")
    print(f"Jumlah tabel terdeteksi: {len(extraction.tables)}")

    for page in extraction.pages:
        print(f"\n{'=' * 24} HALAMAN {page.page_number} {'=' * 24}")
        print(page.text)
        print("\n--- Footer yang terdeteksi ---")
        print(page.footer_text or "(tidak ada teks di area footer)")
        print(f"OCR dipakai: {page.footer_ocr_used}")
        if page.ocr_warning:
            print(f"Catatan OCR: {page.ocr_warning}")

    tables_data = [
        {"pageNumber": table.page_number, "rows": table.rows}
        for table in extraction.tables
    ]
    print("\n--- Tabel terdeteksi (JSON) ---")
    print(json.dumps(tables_data, ensure_ascii=False, indent=2))

    if args.output:
        args.output.write_text(extraction.text, encoding="utf-8")
        print(f"\nTeks disimpan ke: {args.output}")
    if args.tables_output:
        if args.tables_output.resolve() == (PROJECT_ROOT / "tables.json").resolve():
            print(
                "tables.json adalah file acuan; simpan hasil kandidat ke "
                "candidate-tables.json.",
                file=sys.stderr,
            )
            return 2
        args.tables_output.write_text(
            json.dumps(tables_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Tabel disimpan ke: {args.tables_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
