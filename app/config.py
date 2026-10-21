import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str | None = field(repr=False)
    data_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        directory = (
            os.environ.get("TKA_DATA_DIR")
            or os.environ.get("FACTORY_RUNTIME_STATE")
            or ".tka-data"
        )
        return cls(
            database_url=os.environ.get("DATABASE_URL") or None,
            data_dir=Path(directory).expanduser().resolve(),
        )
