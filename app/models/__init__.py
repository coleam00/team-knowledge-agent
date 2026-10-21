"""Model factories. Fake mode never reads credentials or imports a provider."""

from app.config import Settings
from app.models.fake import FakeAnswerer, FakeEmbedder
from app.models.interfaces import AnswerChunk, Answerer, Embedder

__all__ = ["AnswerChunk", "Answerer", "Embedder", "get_answerer", "get_embedder"]


def get_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or Settings.from_env()
    settings.validate_models()
    if settings.models_mode == "fake":
        return FakeEmbedder()
    from app.models.openrouter import OpenRouterEmbedder

    return OpenRouterEmbedder(settings.embedding_model)


def get_answerer(settings: Settings | None = None) -> Answerer:
    settings = settings or Settings.from_env()
    settings.validate_models()
    if settings.models_mode == "fake":
        return FakeAnswerer()
    from app.models.openrouter import OpenRouterAnswerer

    return OpenRouterAnswerer(settings.chat_model)
