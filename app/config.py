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

    # Sandbox for running tools/tests on untrusted PR code: docker | local | off
    sandbox: str = "docker"
    sandbox_image: str = "code-review-sandbox:latest"
    allow_unsafe_local_sandbox: bool = False  # local mode has NO isolation; dev only
    analysis_timeout: int = 120
    test_timeout: int = 180

    # LLM reviewer. Without an API key the reviewer reports tool findings only.
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_temperature: float | None = None  # set to 0 for reproducible benchmark runs
    llm_max_tokens: int = 4096
    max_review_units: int = 10

    # Findings the model is less sure of than this are shown as "needs a human check"
    min_confidence: float = 0.6

    # Fixes: candidate patches are validated in the sandbox before being offered
    enable_fixes: bool = True
    max_fixes: int = 3

    # Publishing
    dry_run: bool = False  # review and log, but post nothing to GitHub
    post_clean_summary: bool = True  # say "found nothing" instead of staying silent
    max_inline_comments: int = 10

    max_concurrent_reviews: int = 2
    data_dir: str = "data"
    log_level: str = "INFO"

    @property
    def private_key_pem(self) -> str:
        """Allow the key to be stored in .env with literal \\n sequences."""
        return self.github_private_key.replace("\\n", "\n")


@lru_cache
def get_settings() -> Settings:
    return Settings()
