from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.public import router as public_router
from app.api.routes import router
from app.api.security import require_admin
from app.config.logging import configure_logging
from app.config.observability import configure_observability
from app.config.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title="Company Risk Data Pipeline",
        version="0.1.0",
        description="Data collection backend for company risk research.",
    )
    # Internal routes need the admin key in production; the website uses /public only.
    application.include_router(router, dependencies=[Depends(require_admin)])
    application.include_router(public_router)
    # The website (a different origin) calls only the read-only /public endpoints and the
    # report request form, so only GET and POST are allowed.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    configure_observability(settings, app=application)
    return application


app = create_app()
