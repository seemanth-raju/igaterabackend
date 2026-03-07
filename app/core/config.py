from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Veda API"
    app_env: str = "development"
    app_debug: bool = True
    create_tables: bool = False

    database_url: str = "postgresql+psycopg2://user:password@localhost:5432/veda_db"

    jwt_secret_key: str = "change-this-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Fernet key for encrypting device API passwords at rest
    encryption_key: str = "bJtuiCja4DnYwXKehteyumXVQ6qXe9lYnLD-zfuTndM="

    # Background log sync interval (seconds between device polls)
    log_sync_interval_seconds: int = 60

    cors_origins: list[str] = ["*"]

    # Local path for storing extracted fingerprint templates
    fingerprint_storage_path: str = "storage/fingerprints"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
