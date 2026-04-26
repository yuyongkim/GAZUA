"""Environment configuration for.gazua API.

Load order (tightest wins):
  1. ~/.gazua/shared.env   (personal shared secrets)
  2. apps/api/.env               (local overrides)
  3. Actual process environment  (CI/CD / shell)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---- Step 1: eagerly load shared.env, then local .env ----
_SHARED_ENV = Path.home() / ".gazua" / "shared.env"
if _SHARED_ENV.is_file():
    load_dotenv(_SHARED_ENV, override=False)

_LOCAL_ENV = Path(__file__).resolve().parent / ".env"
if _LOCAL_ENV.is_file():
    load_dotenv(_LOCAL_ENV, override=False)


class Settings(BaseSettings):
    """Typed settings bag — prefer this over os.environ in code."""

    model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore")

    # Local services (uncommon ports — user runs many projects on 3xxx/5xxx/8xxx)
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=31300)
    web_host: str = Field(default="127.0.0.1")
    web_port: int = Field(default=31380)

    # Tenancy + flags
    platform_owner_id: str = Field(default="self")
    enable_stock_modal: bool = Field(default=True)
    enable_ticker_tape: bool = Field(default=True)

    # Backend
    log_level: str = Field(default="INFO")
    cors_origins_raw: str = Field(
        default=(
            "http://127.0.0.1:31380,http://localhost:31380,"
            "https://gazua.yule.pics,https://alpha.yule.pics"
        ),
        alias="CORS_ORIGINS",
    )

    # Frontend base (exposed for diagnostics only)
    next_public_api_base_url: str = Field(default="http://127.0.0.1:31300")

    # Secrets (optional, from shared.env). Code MUST check for None.
    kis_app_key: str | None = Field(default=None)
    kis_app_secret: str | None = Field(default=None)
    kiwoom_app_key: str | None = Field(default=None)
    kiwoom_secret_key: str | None = Field(default=None)
    dart_api_key: str | None = Field(default=None)
    fred_api_key: str | None = Field(default=None)
    ecos_api_key: str | None = Field(default=None)

    # Tenant control plane — admin token gates create/rotate/disable + mutating
    # reads. When unset, GET endpoints still work (self-only) but mutations are
    # forbidden, matching the spec's "admin auth" requirement.
    ky_admin_token: str | None = Field(default=None)

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]

    @property
    def shared_env_present(self) -> bool:
        return _SHARED_ENV.is_file()


settings = Settings()  # type: ignore[call-arg]
