from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator
from app.core.config import DB_DRIVER, DB_SERVER, DB_DATABASE, DB_UID, DB_PASSWORD

import pyodbc

logger = logging.getLogger(__name__)

def build_connection_string() -> str:
    """
    Membangun connection string SQL Server secara aman:
    - Jika DB_UID dan DB_PASSWORD diisi di .env -> pakai SQL Authentication.
    - Jika salah satu/keduanya kosong -> fallback ke Windows Authentication
      (Trusted_Connection=yes), TIDAK PERNAH menyisipkan literal 'None'
      ke dalam connection string.
    """

    base = (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
    )

    if DB_UID and DB_PASSWORD:

        return base + f"UID={DB_UID};PWD={DB_PASSWORD};TrustServerCertificate=yes;"

    return base + "Trusted_Connection=yes;TrustServerCertificate=yes;"

@contextmanager
def safe_connection(connection_string: str) -> Iterator[pyodbc.Connection]:

    conn = pyodbc.connect(connection_string)

    try:

        yield conn

        conn.commit()

    except Exception:

        try:

            conn.rollback()

        except pyodbc.Error:

            logger.warning("[DB] Rollback gagal setelah error sebelumnya.", exc_info=True)

        raise

    finally:

        try:

            conn.close()

        except pyodbc.Error:

            logger.warning("[DB] Gagal menutup koneksi dengan bersih.", exc_info=True)


def check_database_reachable(connection_string: str) -> bool:
    """
    Ping database ringan untuk keperluan readiness check -- HANYA membuka
    koneksi lalu menutupnya, tanpa query berat. Tidak pernah raise; hasil
    kegagalan direpresentasikan sebagai False.
    """

    try:

        with safe_connection(connection_string) as conn:

            conn.cursor().execute("SELECT 1")

        return True

    except pyodbc.Error as ex:

        logger.error(f"[DB] Readiness check gagal: {type(ex).__name__}")

        return False