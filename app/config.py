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
    ingest_max_file_bytes: int = 1_000_000
    ingest_max_files: int = 5000
    ingest_max_total_bytes: int = 200000000
    ingest_command_timeout_seconds: float = 120
    chunk_window_lines: int = 8
    chunk_overlap_lines: int = 10

    def validate_ingest(self) -> None:
        from app.ingest.contracts import CloneLimits

        CloneLimits(
            self.ingest_max_files,
            self.ingest_max_total_bytes,
            self.ingest_command_timeout_seconds,
            self.ingest_max_file_bytes,
        )
        if type(self.chunk_window_lines) is not int or self.chunk_window_lines <= 0:
            raise ValueError("Chunk window must be a positive integer")
        if (
            type(self.chunk_overlap_lines) is not int
            or not 0 <= self.chunk_overlap_lines < self.chunk_window_lines
        ):
            raise ValueError("Chunk overlap must be in [0, window)")

    min_similarity: float | None = None

    def __post_init__(self) -> None:
        if self.min_similarity is not None and (
            not math.isfinite(self.min_similarity) or not -1 <= self.min_similarity <= 1
        ):
            raise ModelConfigurationError(
                "TKA_MIN_SIMILARITY must be finite in [-1, 1]"
            )

    @property
    def search_min_similarity(self) -> float:
        if self.min_similarity is not None:
            return self.min_similarity
        return 0.25 if self.models_mode == "fake" else 0.35

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
        try:
            minimum = os.environ.get("TKA_MIN_SIMILARITY")
            min_similarity = float(minimum) if minimum is not None else None
        except ValueError:
            raise ModelConfigurationError("Invalid TKA_MIN_SIMILARITY") from None
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
            ingest_max_file_bytes=int(
                os.environ.get(
                    "TKA_INGEST_MAX_FILE_BYTES", str(cls.ingest_max_file_bytes)
                )
            ),
            ingest_max_files=int(
                os.environ.get("TKA_INGEST_MAX_FILES", str(cls.ingest_max_files))
            ),
            ingest_max_total_bytes=int(
                os.environ.get(
                    "TKA_INGEST_MAX_TOTAL_BYTES", str(cls.ingest_max_total_bytes)
                )
            ),
            ingest_command_timeout_seconds=float(
                os.environ.get(
                    "TKA_INGEST_COMMAND_TIMEOUT_SECONDS",
                    str(cls.ingest_command_timeout_seconds),
                )
            ),
            chunk_window_lines=int(
                os.environ.get("TKA_CHUNK_WINDOW_LINES", str(cls.chunk_window_lines))
            ),
            chunk_overlap_lines=int(
                os.environ.get("TKA_CHUNK_OVERLAP_LINES", str(cls.chunk_overlap_lines))
            ),
            min_similarity=min_similarity,
        )
        settings.validate_models()
        settings.validate_ingest()
        return replace(settings, data_dir=settings.data_dir.expanduser().resolve())
