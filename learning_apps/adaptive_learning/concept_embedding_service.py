from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from typing import Iterable, List

from django.conf import settings

from learning_apps.infrastructure.services.llm_gateway import llm_gateway


logger = logging.getLogger(__name__)

FALLBACK_MODEL = "local_lexical_hash_v1"


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: List[List[float]]
    model: str
    dimensions: int
    provider: str
    is_semantic: bool
    degraded: bool = False


def _stable_hash(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16], 16)


def _lexical_terms(text: str) -> List[str]:
    normalized = " ".join(str(text or "").lower().split())
    words = [token for token in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE) if token]
    terms = list(words)
    terms.extend(f"{left}_{right}" for left, right in zip(words, words[1:]))
    # Character trigrams give deterministic multilingual lexical fallback without
    # pretending that this path provides semantic similarity.
    compact = re.sub(r"\s+", "", normalized)
    terms.extend(compact[index : index + 3] for index in range(max(0, len(compact) - 2)))
    return terms


def _normalize_vector(vector: Iterable[float]) -> List[float]:
    values = [float(value or 0.0) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        return [0.0 for _value in values]
    return [round(value / norm, 8) for value in values]


def _fallback_embedding(text: str, dimensions: int) -> List[float]:
    vector = [0.0] * dimensions
    terms = _lexical_terms(text)
    if not terms:
        return vector
    for term in terms:
        digest = _stable_hash(term)
        index = digest % dimensions
        sign = 1.0 if ((digest >> 9) & 1) == 0 else -1.0
        vector[index] += sign
    return _normalize_vector(vector)


class ConceptEmbeddingService:
    @property
    def remote_model(self) -> str:
        return str(getattr(settings, "LEARNING_CONCEPT_EMBEDDING_MODEL", "text-embedding-3-small"))

    @property
    def dimensions(self) -> int:
        return max(64, int(getattr(settings, "LEARNING_CONCEPT_EMBEDDING_DIMENSIONS", 256)))

    @property
    def remote_enabled(self) -> bool:
        return bool(getattr(settings, "LEARNING_CONCEPT_REMOTE_EMBEDDINGS_ENABLED", True))

    def embed_texts(self, texts: Iterable[str], *, allow_remote: bool = True) -> EmbeddingBatch:
        cleaned = [" ".join(str(text or "").split()) for text in texts]
        if not cleaned or any(not text for text in cleaned):
            raise ValueError("Concept embedding input must contain non-empty strings.")

        if allow_remote and self.remote_enabled:
            try:
                vectors = llm_gateway.embeddings_or_raise(
                    route="adaptive.concept_embedding",
                    model=self.remote_model,
                    input_texts=cleaned,
                    dimensions=self.dimensions,
                    metadata={"purpose": "concept_identity"},
                )
                normalized = [_normalize_vector(vector) for vector in vectors]
                if all(len(vector) == self.dimensions for vector in normalized):
                    return EmbeddingBatch(
                        vectors=normalized,
                        model=self.remote_model,
                        dimensions=self.dimensions,
                        provider="openai",
                        is_semantic=True,
                    )
                raise RuntimeError("Concept embedding dimensions did not match the configured size.")
            except Exception as exc:
                logger.warning("Concept semantic embeddings unavailable; using lexical fallback: %s", exc)

        return EmbeddingBatch(
            vectors=[_fallback_embedding(text, self.dimensions) for text in cleaned],
            model=FALLBACK_MODEL,
            dimensions=self.dimensions,
            provider="local",
            is_semantic=False,
            degraded=bool(allow_remote and self.remote_enabled),
        )

    def embed_text(self, text: str, *, allow_remote: bool = True) -> tuple[List[float], EmbeddingBatch]:
        batch = self.embed_texts([text], allow_remote=allow_remote)
        return batch.vectors[0], batch


embedding_service = ConceptEmbeddingService()


__all__ = [
    "ConceptEmbeddingService",
    "EmbeddingBatch",
    "FALLBACK_MODEL",
    "embedding_service",
]
