import math
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

type ErrorCode = Literal[
    "invalid_source",
    "local_sources_disabled",
    "invalid_local_repository",
    "source_mismatch",
    "invalid_storage",
    "busy",
    "not_found",
    "network",
    "empty_repository",
    "git_unavailable",
    "git_failed",
    "timeout",
    "file_count_limit",
    "total_bytes_limit",
]

_MESSAGES: dict[ErrorCode, str] = {
    "invalid_source": "Source must identify a public GitHub repository.",
    "local_sources_disabled": "Local sources are disabled.",
    "invalid_local_repository": "Local repository layout or config is unsupported.",
    "source_mismatch": "Source identity does not match the stored repository.",
    "invalid_storage": "Repository storage is unavailable or unsafe.",
    "busy": "This source is already being acquired.",
    "not_found": "Repository is unavailable to anonymous access.",
    "network": "Repository transport failed.",
    "empty_repository": "Repository has no committed HEAD.",
    "git_unavailable": "Git 2.47 or newer is required.",
    "git_failed": "Git could not read repository data.",
    "timeout": "Repository command exceeded its deadline.",
    "file_count_limit": "Selected file count exceeds the supplied limit.",
    "total_bytes_limit": "Selected content exceeds the supplied byte limit.",
}


class CloneSelectError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(_MESSAGES[code])


@dataclass(frozen=True)
class SourceSpec:
    source_id: UUID
    location: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, UUID) or not isinstance(self.location, str):
            raise CloneSelectError("invalid_source")


@dataclass(frozen=True)
class CloneLimits:
    max_files: int
    max_total_bytes: int
    subprocess_timeout_seconds: float
    max_file_bytes: int = 1_000_000

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (self.max_files, self.max_total_bytes, self.max_file_bytes)
        ):
            raise ValueError("File and byte limits must be positive integers")
        timeout = self.subprocess_timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Subprocess timeout must be positive and finite")


@dataclass(frozen=True)
class SelectedFile:
    path: str
    blob_sha: str
    language: str
    size_bytes: int
    content: str = field(repr=False)


@dataclass(frozen=True)
class SelectionResult:
    source_id: UUID
    repo_url: str
    commit_sha: str
    files: tuple[SelectedFile, ...]
