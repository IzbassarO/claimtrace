"""Runtime configuration from environment / .env. Never log values, only names."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROVIDERS = ("anthropic", "moonshot")
ROLES = ("extract", "judge", "skeptic")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    llm_extract_provider: str = ""
    llm_extract_model: str = ""
    llm_judge_provider: str = ""
    llm_judge_model: str = ""
    llm_skeptic_provider: str = ""
    llm_skeptic_model: str = ""
    anthropic_api_key: SecretStr = SecretStr("")
    moonshot_api_key: SecretStr = SecretStr("")
    moonshot_base_url: str = "https://api.moonshot.ai/v1"

    contact_email: str = ""  # Crossref polite pool, Unpaywall (required there)
    semantic_scholar_api_key: SecretStr = SecretStr("")
    openalex_api_key: SecretStr = SecretStr("")  # required by OpenAlex since Feb 2026
    grobid_url: str = "http://localhost:8070"

    max_upload_mb: int = 20
    max_references: int = 200
    max_claims: int = 150
    llm_concurrency: int = 4

    def problems(self) -> list[str]:
        """Configuration errors, by variable name only (safe to log)."""
        out = []
        for role in ROLES:
            prov = getattr(self, f"llm_{role}_provider")
            if prov not in PROVIDERS:
                out.append(f"LLM_{role.upper()}_PROVIDER must be one of {PROVIDERS}")
            if not getattr(self, f"llm_{role}_model"):
                out.append(f"LLM_{role.upper()}_MODEL is empty")
            if prov in PROVIDERS and not getattr(self, f"{prov}_api_key").get_secret_value():
                out.append(f"{prov.upper()}_API_KEY is empty (needed by {role})")
        if self.llm_judge_provider and self.llm_judge_provider == self.llm_skeptic_provider:
            out.append("LLM_JUDGE_PROVIDER and LLM_SKEPTIC_PROVIDER must differ")
        if not self.openalex_api_key.get_secret_value():
            out.append("OPENALEX_API_KEY is empty (OpenAlex requires it)")
        if not self.contact_email:
            out.append("CONTACT_EMAIL is empty (Unpaywall needs it)")
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()


# `uv run python -m claimtrace.core.settings` checks .env without printing values
if __name__ == "__main__":
    probs = get_settings().problems()
    print("\n".join(probs) if probs else "settings OK")
