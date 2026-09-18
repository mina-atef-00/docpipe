"""Embedder backends: local lexical embedder and HTTP embedder."""

from __future__ import annotations

import json

import httpx
import pytest

from docpipe.embed import (
    EmbeddingBackendError,
    EmbeddingConfigError,
    HashedNgramEmbedder,
    HttpEmbedder,
    embedder_from_meta,
    make_http_embedder,
    make_local_embedder,
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def test_local_embedder_is_deterministic() -> None:
    embedder = make_local_embedder()
    assert embedder.embed_one("the widget service") == embedder.embed_one("the widget service")


def test_local_embedder_vector_is_normalized_and_dim() -> None:
    embedder = make_local_embedder()
    vector = embedder.embed_one("create a new widget through the api")
    assert len(vector) == 384
    norm = sum(x * x for x in vector) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_local_embedder_needs_no_network_or_key() -> None:
    # Constructing and embedding must succeed with no configuration at all.
    embedder = HashedNgramEmbedder()
    assert embedder.embed_one("hello") == embedder.embed_one("hello")


def test_local_embedder_similar_texts_are_closer() -> None:
    embedder = make_local_embedder()
    a = embedder.embed_one("the queue delivers widget change notifications")
    b = embedder.embed_one("queue service change notifications for widgets")
    c = embedder.embed_one("backup the store snapshot nightly")
    assert _cosine(a, b) > _cosine(a, c)


def test_http_embedder_requires_api_key() -> None:
    with pytest.raises(EmbeddingConfigError):
        HttpEmbedder(base_url="https://example.com", model="m", api_key="")


def test_make_http_embedder_requires_base_url_and_model() -> None:
    with pytest.raises(EmbeddingConfigError):
        make_http_embedder(base_url="", model="m", api_key="k")
    with pytest.raises(EmbeddingConfigError):
        make_http_embedder(base_url="https://example.com", model="", api_key="k")


def _mock_handler(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    inputs = payload["input"]
    data = [
        {"object": "embedding", "index": i, "embedding": [float(i + 1)] * 8}
        for i in range(len(inputs))
    ]
    return httpx.Response(200, json={"object": "list", "data": data, "model": payload["model"]})


def test_http_embedder_roundtrips_via_mock_transport() -> None:
    embedder = HttpEmbedder(
        base_url="https://example.com",
        model="test-model",
        api_key="secret",
        transport=httpx.MockTransport(_mock_handler),
    )
    vectors = embedder.embed(["first text", "second text"])
    assert len(vectors) == 2
    assert vectors[0] == [1.0] * 8
    assert vectors[1] == [2.0] * 8
    assert embedder.dim == 8


def test_http_embedder_network_error_raises_clearly() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    embedder = HttpEmbedder(
        base_url="https://example.com",
        model="test-model",
        api_key="secret",
        transport=httpx.MockTransport(failing_handler),
    )
    with pytest.raises(EmbeddingBackendError):
        embedder.embed(["text"])


def test_http_embedder_never_falls_back_to_local() -> None:
    # A misconfigured http embedder raises; it does not silently use the local one.
    with pytest.raises(EmbeddingConfigError):
        embedder_from_meta(
            {"embedder": "http", "embed_base_url": "", "embed_model": ""}, api_key=""
        )


def test_embedder_from_meta_rebuilds_local_by_default() -> None:
    embedder = embedder_from_meta({})
    assert embedder.name == "hashed-ngram"
    assert embedder.dim == 384
