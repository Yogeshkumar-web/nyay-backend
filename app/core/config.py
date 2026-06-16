import secrets
from typing import List, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.production"),
        extra="ignore",
        case_sensitive=True,
    )

    # ─────────────────────────────────────────────
    # Core App
    # ─────────────────────────────────────────────
    APP_ENV: str = "development"
    DEBUG: bool = True

    # ─────────────────────────────────────────────
    # Security
    # ─────────────────────────────────────────────
    SECRET_KEY: str = secrets.token_hex(32)
    ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    COOKIE_DOMAIN: str | None = None
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"

    # ─────────────────────────────────────────────
    # Database
    # ─────────────────────────────────────────────
    DATABASE_URL: str

    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # ─────────────────────────────────────────────
    # Redis
    # ─────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ─────────────────────────────────────────────
    # CORS
    # ─────────────────────────────────────────────
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # ─────────────────────────────────────────────
    # Cloudflare R2
    # ─────────────────────────────────────────────
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "vakilsuite-dev"

    # ─────────────────────────────────────────────
    # Google Cloud
    # ─────────────────────────────────────────────
    GOOGLE_PROJECT_ID: str = ""
    GOOGLE_LOCATION: str = "us"
    GOOGLE_DOCAI_PROCESSOR_ID: str = ""
    GOOGLE_DOCAI_FIR_PROCESSOR_ID: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_VISION_API_KEY: str = ""

    # ─────────────────────────────────────────────
    # AI Provider
    # "gemini"  → Google Gemini (development)
    # AI provider routing — set AI_PROVIDER in .env to switch models
    # Supported values:
    #   "gemini"   → Google Gemini  (good free tier, multimodal)
    #   "claude"   → Anthropic Claude (production quality, multimodal)
    #   "deepseek" → DeepSeek via OpenAI-compat API (cheap, text-only)
    #   "openai"   → OpenAI GPT (text + vision)
    # ─────────────────────────────────────────────
    AI_PROVIDER: str = "gemini"

    # Google Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"  # gemini-2.5-flash has 20 req/day free limit

    # Anthropic Claude
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = (
        "claude-haiku-4-5"  # claude-haiku-4-5 (fast), claude-sonnet-4-5 (balanced)
    )

    # DeepSeek  (OpenAI-compatible, text-only — no image support)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"  # deepseek-chat (V3), deepseek-reasoner (R1)
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"  # gpt-4o-mini (cheap), gpt-4o (quality)

    # ─────────────────────────────────────────────
    # Sentry
    # ─────────────────────────────────────────────
    SENTRY_DSN: str = ""

    # ─────────────────────────────────────────────
    # Validators (Production Safety)
    # ─────────────────────────────────────────────
    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("CORS_ORIGINS cannot be empty")
        return v

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, v):
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized in {"release", "prod", "production"}:
                return False
            if normalized in {"debug", "dev", "development"}:
                return True
        return v

    def validate_production(self) -> None:
        """
        Hard fail if production config is unsafe.
        """
        if self.APP_ENV == "production":
            if self.SECRET_KEY.startswith("change-me") or len(self.SECRET_KEY) < 32:
                raise RuntimeError("❌ SECRET_KEY is not secure")

            if self.DEBUG:
                raise RuntimeError("❌ DEBUG must be False in production")

            if "localhost" in self.DATABASE_URL:
                raise RuntimeError(
                    "❌ DATABASE_URL cannot point to localhost in production"
                )

            if not self.SENTRY_DSN:
                raise RuntimeError("❌ SENTRY_DSN required in production")

            if not self.COOKIE_SECURE:
                raise RuntimeError("❌ COOKIE_SECURE must be True in production")

        if self.COOKIE_SAMESITE == "none" and not self.COOKIE_SECURE:
            raise RuntimeError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")


settings = Settings()

# 🔥 Fail fast on startup
settings.validate_production()


# from pydantic_settings import BaseSettings, SettingsConfigDict


# class Settings(BaseSettings):
#     model_config = SettingsConfigDict(env_file=".env", extra="ignore")

#     # Database
#     DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vakilsuite"
#     DB_ECHO: bool = False

#     # JWT
#     SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
#     ALGORITHM: str = "HS256"
#     ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
#     REFRESH_TOKEN_EXPIRE_DAYS: int = 30

#     # Redis
#     REDIS_URL: str = "redis://localhost:6379/0"

#     # CORS
#     CORS_ORIGINS: list[str] = ["http://localhost:3000"]

#     # Cloudflare R2
#     R2_ACCOUNT_ID: str = ""
#     R2_ACCESS_KEY_ID: str = ""
#     R2_SECRET_ACCESS_KEY: str = ""
#     R2_BUCKET_NAME: str = "vakilsuite-dev"

#     # Google Cloud & OAuth
#     GOOGLE_PROJECT_ID: str = ""
#     GOOGLE_LOCATION: str = "us"
#     GOOGLE_DOCAI_PROCESSOR_ID: str = ""
#     GOOGLE_CLIENT_ID: str = ""
#     GOOGLE_VISION_API_KEY: str = ""

#     # Anthropic
#     ANTHROPIC_API_KEY: str = ""

#     # App
#     APP_ENV: str = "development"
#     DEBUG: bool = True

#     # Sentry
#     SENTRY_DSN: str = ""


# settings = Settings()
