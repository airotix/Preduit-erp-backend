"""Application settings, loaded from environment / .env (see .env.example)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"

    # Database (PostgreSQL)
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "preduit"
    db_app_user: str = "erp_app"
    db_app_password: str = ""
    db_system_user: str = "erp_system"
    db_system_password: str = ""
    db_sslmode: str = "prefer"

    # Entra External ID
    entra_tenant_id: str = ""
    entra_authority: str = ""
    entra_api_audience: str = ""
    entra_openid_config: str = ""

    # Self-managed auth (app-issued JWT). Override JWT_SECRET in prod.
    jwt_secret: str = "dev-insecure-change-me-please"
    jwt_algorithm: str = "HS256"
    jwt_access_minutes: int = 30
    jwt_refresh_days: int = 14

    # Refresh token delivery. The refresh token is sent as an HttpOnly cookie
    # (JS can't read it → not exfiltratable via XSS) rather than in the JSON
    # body. The browser reaches the API same-origin through the Next.js proxy,
    # so a first-party SameSite=Lax cookie works in dev (http) and prod (https).
    refresh_cookie_name: str = "erp_refresh"
    refresh_cookie_path: str = "/api/v1/auth"     # only sent to refresh/logout/me
    refresh_cookie_samesite: str = "lax"

    @property
    def refresh_cookie_secure(self) -> bool:
        # Secure (HTTPS-only) everywhere except local dev over http.
        return self.env != "dev"

    # Auth hardening (AUTH-E).
    auth_max_failed_attempts: int = 5      # failed logins before a temporary lock
    auth_lockout_minutes: int = 15         # how long the account stays locked
    rate_limit_enabled: bool = True        # sliding-window throttle on sensitive endpoints
    rate_limit_window_seconds: int = 60
    rate_limit_max_attempts: int = 10      # per window, per client+bucket
    # Comma-separated browser origins allowed to call the API (credentialed CORS).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # File uploads (documents module). Reject anything larger than the cap or
    # whose extension/MIME isn't on the allowlist.
    upload_max_bytes: int = 15 * 1024 * 1024   # 15 MB
    upload_allowed_exts: str = (
        "png,jpg,jpeg,gif,webp,svg,pdf,csv,txt,doc,docx,xls,xlsx,ppt,pptx"
    )
    upload_allowed_mimes: str = (
        "image/png,image/jpeg,image/gif,image/webp,image/svg+xml,"
        "application/pdf,text/csv,text/plain,"
        "application/msword,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.ms-excel,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/vnd.ms-powerpoint,"
        "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
        "application/octet-stream"   # some browsers send this for known-good files
    )

    @property
    def upload_ext_set(self) -> set[str]:
        return {e.strip().lower().lstrip(".") for e in self.upload_allowed_exts.split(",") if e.strip()}

    @property
    def upload_mime_set(self) -> set[str]:
        return {m.strip().lower() for m in self.upload_allowed_mimes.split(",") if m.strip()}

    @property
    def jwt_secret_is_default(self) -> bool:
        return self.jwt_secret == "dev-insecure-change-me-please"

    # Dev-only login bypass (NEVER enable outside local dev).
    # When true, requests are authenticated as a fixed fake principal so the API
    # can run without Entra. dev_tenant_id should match a real tenant in the DB.
    dev_auth_bypass: bool = False
    dev_tenant_id: str = ""
    dev_external_id: str = "dev-user"
    dev_email: str = "dev@preduit.local"

    # AI Insights → external Forcaster forecasting engine.
    # The backend converses with the engine and materialises its responses into
    # the ai_snapshot tables; the browser never calls the engine directly.
    # ai_engine_enabled=False keeps the AI tabs running with empty data while the
    # engine isn't live yet (no calls, no timeouts, no crashes). Flip to True once
    # the engine is reachable at ai_engine_url.
    ai_engine_enabled: bool = False
    ai_engine_url: str = "http://ai-engine.invalid/api"
    ai_engine_token: str = ""

    # FX rates provider — ExchangeRate-API (exchangerate-api.com). Supports any
    # base currency (incl. PKR) and ~160 currencies. Free key required. Used to
    # populate the dated exchange_rates table for currency conversion.
    #   endpoint: {fx_provider_url}/{fx_api_key}/latest/{BASE}
    fx_provider_url: str = "https://v6.exchangerate-api.com/v6"
    fx_api_key: str = ""

    # Email (generic SMTP). When SMTP_HOST + a from-address are set, the auth
    # flows send real mail; otherwise they fall back to the dev-code behaviour.
    smtp_host: str = ""
    smtp_port: int = 587           # 587 = STARTTLS, 465 = implicit SSL
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True      # STARTTLS on port 587 (ignored for 465)
    mail_from: str = ""            # defaults to smtp_user when blank
    mail_from_name: str = "Preduit Retail"
    # Public URL of the frontend — used to build reset / invite links in emails.
    app_base_url: str = "http://localhost:3000"

    @property
    def mail_sender(self) -> str:
        return self.mail_from or self.smtp_user

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.mail_sender)

    # Cache
    redis_url: str = "redis://localhost:6379/0"

    # Document storage (local dir for dev; S3 in prod).
    # Set S3_BUCKET to enable S3 mode; leave blank for local filesystem.
    doc_storage_dir: str = "./storage"
    s3_bucket: str = ""
    s3_region: str = ""
    s3_endpoint_url: str = ""

    def _pg_url(self, user: str, password: str) -> str:
        return (
            f"postgresql+psycopg2://{user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?sslmode={self.db_sslmode}"
        )

    @property
    def app_database_url(self) -> str:
        """Runtime connection — subject to Row-Level Security."""
        return self._pg_url(self.db_app_user, self.db_app_password)

    @property
    def system_database_url(self) -> str:
        """Provisioning connection — exempt from RLS (erp_system principal)."""
        return self._pg_url(self.db_system_user, self.db_system_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
