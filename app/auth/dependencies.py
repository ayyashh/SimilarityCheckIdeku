from __future__ import annotations

import logging

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.auth.jwt_service import decode_token, InvalidTokenError

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


def require_jwt_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> None:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Invalid or expired API token.")

    try:
        decode_token(credentials.credentials)
    except InvalidTokenError:
        logger.warning("[JWT AUTH] Token rejected (invalid/expired/wrong issuer-audience).")
        raise HTTPException(status_code=401, detail="Invalid or expired API token.")
