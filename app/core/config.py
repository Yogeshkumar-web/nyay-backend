from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vakilsuite"
    DB_ECHO: bool = False

    # JWT
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Cloudflare R2
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "vakilsuite-dev"

    # Google Document AI
    GOOGLE_PROJECT_ID: str = ""
    GOOGLE_LOCATION: str = "us"
    GOOGLE_DOCAI_PROCESSOR_ID: str = ""

    # Anthropic
    ANTHROPIC_API_KEY: str = ""

    # App
    APP_ENV: str = "development"
    DEBUG: bool = True


settings = Settings()