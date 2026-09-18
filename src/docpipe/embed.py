"""Embedding backends for docpipe.

docpipe keeps two embedders behind one interface:

* :class:`HashedNgramEmbedder` is the default. It is a deterministic, local
  lexical vectoriser: character trigrams and whole-word unigrams are hashed into
  a fixed-width signed projection and L2 normalised. It needs no network and no
  API key, so CI and tests always run. It is not a neural embedding; similarity
  reflects surface lexical overlap, not meaning.
* :class:`HttpEmbedder` talks to an external embedding API (OpenAI-compatible
  ``/embeddings`` shape). It is selected explicitly by configuration and never
  falls back to the local backend: a missing key or a failed request is a clear
  error, not a silent downgrade.

The two backends are interchangeable behind the :class:`Embedder` protocol, but
an index records which backend produced its vectors, so query-time search must
use the same one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, runtime_checkable

import httpx

from .chunking import tokenize

LOCAL_EMBEDDER_NAME = "hashed-ngram"
HTTP_EMBEDDER_NAME = "http"
LOCAL_DIM = 384

_NGRAM_SIZES = (3,)
_BOUNDARY = ("^", "$")


class EmbeddingError(Exception):
    """Base class for embedding failures."""


class EmbeddingConfigError(EmbeddingError):
    """The embedder is misconfigured (for example, missing API key)."""


class EmbeddingBackendError(EmbeddingError):
    """The embedder backend failed (for example, network or HTTP error)."""


@runtime_checkable
class Embedder(Protocol):
    """Something that turns text into fixed-width float vectors."""

    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed *texts* and return one vector per text, in input order."""

    def embed_one(self, text: str) -> list[float]:
        """Embed a single string."""


def _feature_strings(text: str) -> list[str]:
    """Return the lexical features (word unigrams and char n-grams) for *text*.

    Every feature is prefixed so a whole-word feature can never collide with a
    character n-gram feature of the same spelling.
    """
    features: list[str] = []
    for token in tokenize(text):
        features.append(f"w:{token}")
        padded = f"{_BOUNDARY[0]}{token}{_BOUNDARY[1]}"
        for size in _NGRAM_SIZES:
            for i in range(len(padded) - size + 1):
                features.append(f"c{size}:{padded[i : i + size]}")
    return features


def _signed_bucket(feature: str, dim: int) -> tuple[int, int]:
    """Map a feature string to a deterministic ``(index, sign)`` pair.

    Uses a fixed BLAKE2b digest so the projection is stable across runs and
    Python processes, with no dependence on ``PYTHONHASHSEED``.
    """
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    index = int.from_bytes(digest[:4], "big") % dim
    sign = 1 if digest[4] & 1 else -1
    return index, sign


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


class HashedNgramEmbedder:
    """Deterministic local lexical embedder (feature hashing over n-grams).

    This is a lexical embedding, not a neural one. Two texts that share words
    and character n-grams produce similar vectors; two texts that mean the same
    thing but use entirely different words generally do not.
    """

    name = LOCAL_EMBEDDER_NAME

    def __init__(self, dim: int = LOCAL_DIM) -> None:
        if dim <= 0:
            raise EmbeddingConfigError(f"embedding dim must be positive, got {dim}")
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            counts = [0.0] * self.dim
            for feature in _feature_strings(text):
                index, sign = _signed_bucket(feature, self.dim)
                counts[index] += sign
            vectors.append(_l2_normalize(counts))
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class HttpEmbedder:
    """Embedder backed by an OpenAI-compatible ``/embeddings`` endpoint.

    ``api_key`` is required; the constructor raises when it is absent rather
    than silently falling back to the local backend. ``transport`` is an
    optional :class:`httpx.BaseTransport` used to inject a mock in tests.
    """

    name = HTTP_EMBEDDER_NAME

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        dim: int | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise EmbeddingConfigError(
                "the http embedder requires an API key; set --embedding-api-key "
                "or DOCPIPE_EMBEDDING_API_KEY"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.dim = dim if dim is not None else 0
        self._timeout = timeout
        self._transport = transport

    def _request(self, texts: list[str]) -> list[list[float]]:
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"input": texts, "model": self.model}
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise EmbeddingBackendError(f"embedding request to {url} failed: {exc}") from exc
        if response.status_code != 200:
            raise EmbeddingBackendError(
                f"embedding endpoint {url} returned {response.status_code}: {response.text[:200]}"
            )
        try:
            body = response.json()
            data = body["data"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingBackendError(f"unexpected embedding response from {url}") from exc
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for item in ordered:
            vector = [float(value) for value in item["embedding"]]
            if self.dim and len(vector) != self.dim:
                raise EmbeddingBackendError(
                    f"embedding endpoint returned dim {len(vector)}, expected {self.dim}"
                )
            vectors.append(vector)
        if len(vectors) != len(texts):
            raise EmbeddingBackendError(
                f"embedding endpoint returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return vectors

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._request(texts)
        if self.dim == 0 and vectors:
            self.dim = len(vectors[0])
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


def make_local_embedder(dim: int = LOCAL_DIM) -> HashedNgramEmbedder:
    """Build the default local lexical embedder."""
    return HashedNgramEmbedder(dim=dim)


def make_http_embedder(
    base_url: str,
    model: str,
    api_key: str,
    dim: int | None = None,
) -> HttpEmbedder:
    """Build the HTTP embedder; raises when the configuration is incomplete."""
    if not base_url:
        raise EmbeddingConfigError(
            "the http embedder requires a base URL; set --embedding-base-url "
            "or DOCPIPE_EMBEDDING_BASE_URL"
        )
    if not model:
        raise EmbeddingConfigError(
            "the http embedder requires a model name; set --embedding-model "
            "or DOCPIPE_EMBEDDING_MODEL"
        )
    return HttpEmbedder(base_url=base_url, model=model, api_key=api_key, dim=dim)


def embedder_to_meta(embedder: Embedder) -> list[tuple[str, str]]:
    """Return ``(key, value)`` meta rows describing an embedder's configuration."""
    rows: list[tuple[str, str]] = [
        ("embedder", embedder.name),
        ("embed_dim", str(embedder.dim)),
    ]
    if isinstance(embedder, HttpEmbedder):
        rows.append(("embed_base_url", embedder.base_url))
        rows.append(("embed_model", embedder.model))
    return rows


def embedder_from_meta(meta: dict[str, str], api_key: str | None = None) -> Embedder:
    """Rebuild the embedder recorded in an index's meta table.

    ``api_key`` is only required for HTTP backends and is never stored in the
    index. The local backend needs nothing from the caller.
    """
    name = meta.get("embedder", LOCAL_EMBEDDER_NAME)
    dim = int(meta.get("embed_dim", LOCAL_DIM))
    if name == HTTP_EMBEDDER_NAME:
        key = api_key or ""
        return make_http_embedder(
            base_url=meta.get("embed_base_url", ""),
            model=meta.get("embed_model", ""),
            api_key=key,
            dim=dim,
        )
    return make_local_embedder(dim=dim)


def serialize_vector(vector: list[float]) -> str:
    """Serialize a vector to the compact JSON string sqlite-vec expects."""
    return json.dumps(vector, separators=(",", ":"))
