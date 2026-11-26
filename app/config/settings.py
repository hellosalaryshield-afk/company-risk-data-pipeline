from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str | None = None
    news_api_key: str | None = None
    data_gov_api_key: str | None = None
    report_footer_note: str | None = None
    apify_token: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    logfire_token: str | None = None
    # Website origins allowed to call the public API, comma separated.
    cors_origins: str = "https://salary-shield.netlify.app,http://localhost:5500,http://127.0.0.1:5500"
    # Outgoing mail for emailed reports. Any SMTP server works (Gmail app password, Brevo).
    # Without smtp_host the email is written to reports/outbox instead of being sent.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    report_from_email: str | None = None
    report_reply_to: str | None = None
    # Google Apps Script web app that sends mail over HTTPS (scripts/gmail_relay.gs).
    # Used instead of SMTP when set, since Render's free plan blocks SMTP ports.
    mail_relay_url: str | None = None
    mail_relay_key: str | None = None
    # Internal (non-/public) routes need this key in an X-Admin-Key header. When it is not
    # set they stay open in development and are closed in production.
    admin_api_key: str | None = None
    # Fetch Glassdoor (paid, via Apify) the first time a company is searched on the website,
    # at most this many calls per calendar month. Off unless switched on.
    glassdoor_on_search: bool = False
    glassdoor_monthly_limit: int = 450

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
