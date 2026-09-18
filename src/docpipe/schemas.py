"""Pydantic response models for the FastAPI service."""

from __future__ import annotations

from pydantic import BaseModel


class Health(BaseModel):
    status: str
    index: str
    documents: int
    chunks: int


class SearchHit(BaseModel):
    doc_id: str
    rel_path: str
    chunk_index: int
    snippet: str
    positions: list[int]
    score: float | None = None


class SearchResponse(BaseModel):
    query: str
    count: int
    results: list[SearchHit]


class DocumentChunk(BaseModel):
    chunk_index: int
    text: str


class DocumentDetail(BaseModel):
    doc_id: str
    rel_path: str
    sha256: str
    size: int
    kind: str
    chunks: list[DocumentChunk]


class DocumentSummary(BaseModel):
    doc_id: str
    rel_path: str
    kind: str
    size: int
    chunks: int


class Stats(BaseModel):
    documents: int
    chunks: int
    terms: int
    run_id: str
