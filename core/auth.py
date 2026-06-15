import os
import secrets

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer()


def require_api_key(creds: HTTPAuthorizationCredentials = Security(_bearer)) -> str:
    expected = os.getenv("API_SECRET_KEY", "")
    if not expected:
        raise HTTPException(status_code=500, detail="API_SECRET_KEY non configurée")
    if not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(status_code=401, detail="Token invalide")
    return creds.credentials
