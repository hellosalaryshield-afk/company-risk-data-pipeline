"""Keep the internal routes private.

The website only needs /public. Everything else (collection runs, paid Glassdoor lookups,
raw reports) is for the team, so it needs the admin key once the app runs in production.
"""

import secrets

from fastapi import Header, HTTPException, Request

from app.config.settings import get_settings

OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def require_admin(request: Request, x_admin_key: str | None = Header(default=None)) -> None:
    if request.url.path in OPEN_PATHS:
        return
    settings = get_settings()
    if settings.admin_api_key:
        if not x_admin_key or not secrets.compare_digest(x_admin_key, settings.admin_api_key):
            raise HTTPException(status_code=401, detail="Admin key required.")
        return
    if settings.app_env == "production":
        raise HTTPException(status_code=403, detail="Internal routes are disabled: no admin key is configured.")
