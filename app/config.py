import os
from dataclasses import dataclass, field, replace
from pathlib import Path


class ModelConfigurationError(ValueError):
    pass


def openrouter_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key.strip():
        raise ModelConfigurationError("OPENROUTER_API_KEY is required in real mode")
    if not key.isascii() or not key.isprintable() or " " in key:
        raise ModelConfigurationError("OPENROUTER_API_KEY must be a valid bearer token")
    return key


@dataclass(frozen=True)
class Settings:
    database_url: str | None = field(repr=False)
    data_dir: Path
    models_mode: str = "real"
    chat_model: str = "anthropic/claude-sonnet-5.5"
    embedding_model: str = "openai/text-embedding-3-small"
    allow_local_sources: bool = False

    def validate_models(self) -> None:
        if self.models_mode not in {"fake", "real"}:
            raise ModelConfigurationError("TKA_MODELS must be fake or real")
        if self.models_mode == "real":
            openrouter_api_key()
            if not self.chat_model.strip() or not self.embedding_model.strip():
                raise ModelConfigurationError(
                    "TKA_CHAT_MODEL and TKA_EMBEDDING_MODEL must be nonempty"
                )

    @classmethod
    def from_env(cls) -> "Settings":
        directory = (
            os.environ.get("TKA_DATA_DIR")
            or os.environ.get("FACTORY_RUNTIME_STATE")
            or ".tka-data"
        )
        settings = cls(
            database_url=os.environ.get("DATABASE_URL") or None,
            data_dir=Path(directory),
            models_mode=os.environ.get("TKA_MODELS", "real"),
            chat_model=os.environ.get("TKA_CHAT_MODEL", cls.chat_model),
            embedding_model=os.environ.get("TKA_EMBEDDING_MODEL", cls.embedding_model),
            allow_local_sources=os.environ.get("TKA_ALLOW_LOCAL_SOURCES") == "1",
        )
        settings.validate_models()
        return replace(settings, data_dir=settings.data_dir.expanduser().resolve())
