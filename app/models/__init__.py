"""Model factories. Fake mode never reads credentials or imports a provider."""

from app.config import Settings
from app.models.fake import FakeAnswerer, FakeEmbedder
from app.models.interfaces import AnswerChunk, Answerer, Embedder

__all__ = ["AnswerChunk", "Answerer", "Embedder", "get_answerer", "get_embedder"]


def _require_fake(settings: Settings | None) -> None:
    mode = (settings or Settings.from_env()).models_mode
    if mode == "real":
        raise NotImplementedError("Real model providers are not implemented yet")
    if mode != "fake":
        raise ValueError("TKA_MODELS must be fake or real")


def get_embedder(settings: Settings | None = None) -> Embedder:
    _require_fake(settings)
    return FakeEmbedder()


def get_answerer(settings: Settings | None = None) -> Answerer:
    _require_fake(settings)
    return FakeAnswerer()
