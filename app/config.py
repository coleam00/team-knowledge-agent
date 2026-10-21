import math
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
    ingest_max_files: int = 5000
    ingest_max_total_bytes: int = 200000000
    ingest_command_timeout_seconds: float = 120
    chunk_window_lines: int = 80
    chunk_overlap_lines: int = 10

    def validate_ingest(self) -> None:
        for value in (
            self.ingest_max_files,
            self.ingest_max_total_bytes,
            self.chunk_window_lines,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(
                    "Ingest limits and chunk window must be positive integers"
                )
        timeout = self.ingest_command_timeout_seconds
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Ingest timeout must be positive and finite")
        if (
            type(self.chunk_overlap_lines) is not int
            or not 0 <= self.chunk_overlap_lines < self.chunk_window_lines
        ):
            raise ValueError("Chunk overlap must be in [0, window)")

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
            ingest_max_files=int(os.environ.get("TKA_INGEST_MAX_FILES", "5000")),
            ingest_max_total_bytes=int(
                os.environ.get("TKA_INGEST_MAX_TOTAL_BYTES", "200000000")
            ),
            ingest_command_timeout_seconds=float(
                os.environ.get("TKA_INGEST_COMMAND_TIMEOUT_SECONDS", "120")
            ),
            chunk_window_lines=int(os.environ.get("TKA_CHUNK_WINDOW_LINES", "80")),
            chunk_overlap_lines=int(os.environ.get("TKA_CHUNK_OVERLAP_LINES", "10")),
        )
        settings.validate_models()
        settings.validate_ingest()
        return replace(settings, data_dir=settings.data_dir.expanduser().resolve())
