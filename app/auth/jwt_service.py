from __future__ import annotations

import time
from typing import Any, Dict

import jwt

from app.core.config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    JWT_EXPIRE_MINUTES,
    JWT_ISSUER,
    JWT_AUDIENCE,
)


class InvalidTokenError(Exception):
    """Token tidak valid (signature/expired/issuer/audience/malformed)."""


def create_token(subject: str = "similarity-api-client") -> str:

    if not JWT_SECRET_KEY or JWT_SECRET_KEY == "replace-with-a-long-random-secret":
        raise RuntimeError(
            "JWT_SECRET_KEY belum dikonfigurasi. Isi .env dengan secret acak yang kuat."
        )

    now = int(time.time())

    payload: Dict[str, Any] = {
        "sub": subject,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + (JWT_EXPIRE_MINUTES * 60),
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:

    try:

        return jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )

    except jwt.PyJWTError as ex:

        raise InvalidTokenError(str(ex))
