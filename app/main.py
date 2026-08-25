from fastapi import FastAPI

from app.api.routes import router
from app.config.logging import configure_logging
from app.config.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    application = FastAPI(
        title="Company Risk Data Pipeline",
        version="0.1.0",
        description="Data collection backend for company risk research.",
    )
    application.include_router(router)
    return application


app = create_app()
