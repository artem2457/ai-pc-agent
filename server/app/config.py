from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    secret_key: str = "change-me-in-production"
    database_url: str = f"sqlite:///{ROOT / 'data' / 'app.db'}"
    public_url: str = "http://localhost:8000"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    token_expire_hours: int = 72

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")


settings = Settings()
