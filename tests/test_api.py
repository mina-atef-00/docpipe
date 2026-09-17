"""FastAPI service endpoints via TestClient."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from docpipe.api import create_app


def test_health(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    client = TestClient(create_app(index))
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["documents"] > 0


def test_search_endpoint(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    client = TestClient(create_app(index))
    response = client.get("/search", params={"q": "alpha"})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "alpha"
    assert body["count"] >= 1
    assert body["results"][0]["rel_path"] == "a/one.md"


def test_documents_list(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    client = TestClient(create_app(index))
    response = client.get("/documents")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_document_detail(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    client = TestClient(create_app(index))
    docs = client.get("/documents").json()
    doc_id = docs[0]["doc_id"]
    response = client.get(f"/documents/{doc_id}")
    assert response.status_code == 200
    assert response.json()["doc_id"] == doc_id


def test_document_404(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    client = TestClient(create_app(index))
    response = client.get("/documents/" + "f" * 64)
    assert response.status_code == 404


def test_stats_endpoint(pipeline: tuple[Path, Path, Path, Path]) -> None:
    _corpus, _m, _c, index = pipeline
    client = TestClient(create_app(index))
    response = client.get("/stats")
    assert response.status_code == 200
    assert len(response.json()["run_id"]) == 64
