"""Application settings, loaded from environment / .env (see .env.example)."""
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"

    # Database
    # sql_server may be "localhost", "localhost\\SQLEXPRESS", or an Azure FQDN.
    sql_server: str = "localhost"
    sql_database: str = "preduit"
    sql_app_user: str = "erp_app"
    sql_app_password: str = ""
    sql_system_user: str = "erp_system"
    sql_system_password: str = ""
    # Local SQL Server uses a self-signed cert → trust it. On Azure set encrypt=yes, trust=no.
    sql_encrypt: str = "yes"
    sql_trust_server_cert: str = "yes"
    # Windows auth (Trusted_Connection) for local dev — e.g. LocalDB. When "yes",
    # Uid/Pwd are ignored and the process's Windows identity is used.
    sql_trusted_connection: str = "no"

    # Entra External ID
    entra_tenant_id: str = ""
    entra_authority: str = ""
    entra_api_audience: str = ""
    entra_openid_config: str = ""

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

    # Cache
    redis_url: str = "redis://localhost:6379/0"

    # Document storage (local dir for dev; Azure Blob in prod)
    doc_storage_dir: str = "./storage"

    def _odbc(self, user: str, password: str) -> str:
        parts = [
            "Driver={ODBC Driver 18 for SQL Server}",
            f"Server={self.sql_server}",
            f"Database={self.sql_database}",
        ]
        if self.sql_trusted_connection.lower() in ("yes", "true", "1"):
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"Uid={user}")
            parts.append(f"Pwd={password}")
        parts += [
            f"Encrypt={self.sql_encrypt}",
            f"TrustServerCertificate={self.sql_trust_server_cert}",
            "Connection Timeout=30",
        ]
        conn = ";".join(parts) + ";"
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(conn)}"

    @property
    def app_database_url(self) -> str:
        """Runtime connection — subject to Row-Level Security."""
        return self._odbc(self.sql_app_user, self.sql_app_password)

    @property
    def system_database_url(self) -> str:
        """Provisioning connection — exempt from RLS (erp_system principal)."""
        return self._odbc(self.sql_system_user, self.sql_system_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
