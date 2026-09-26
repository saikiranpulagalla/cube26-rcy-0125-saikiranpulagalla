from __future__ import annotations

from fastapi import Header, HTTPException, status

from recovery_manager.config import Principal, Settings, get_settings


def authenticate_development_credential(
    x_development_credential: str | None = Header(default=None),
    settings: Settings | None = None,
) -> Principal:
    current = settings or get_settings()
    if x_development_credential is None or not x_development_credential.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing development credential"
        )
    principal = current.principals().get(x_development_credential)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown development credential"
        )
    return principal
