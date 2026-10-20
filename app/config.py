"""Application settings, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CRA_", extra="ignore")

    # GitHub App credentials
    github_app_id: str = ""
    github_private_key: str = ""  # PEM contents (use \n escapes in .env)
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"

    # Reviewer behaviour (used in later parts)
    min_confidence: float = 0.6

    log_level: str = "INFO"

    @property
    def private_key_pem(self) -> str:
        """Allow the key to be stored in .env with literal \\n sequences."""
        return self.github_private_key.replace("\\n", "\n")


@lru_cache
def get_settings() -> Settings:
    return Settings()
