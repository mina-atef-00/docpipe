"""FastAPI service exposing search, document fetch and health endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from . import query as query_module
from .indexer import connect_readonly
from .schemas import (
    DocumentDetail,
    DocumentSummary,
    Health,
    SearchHit,
    SearchResponse,
    Stats,
)


def create_app(index_path: Path) -> FastAPI:
    app = FastAPI(title="docpipe", version="0.1.0")

    @app.get("/health", response_model=Health)
    def health() -> Health:
        conn = connect_readonly(index_path)
        try:
            current = query_module.stats(conn)
        finally:
            conn.close()
        return Health(
            status="ok",
            index=str(index_path),
            documents=current["documents"],
            chunks=current["chunks"],
        )

    @app.get("/search", response_model=SearchResponse)
    def search(
        q: str = Query(..., min_length=1),
        limit: int = Query(20, ge=1, le=100),
    ) -> SearchResponse:
        conn = connect_readonly(index_path)
        try:
            hits = query_module.search(conn, q, limit)
        finally:
            conn.close()
        return SearchResponse(
            query=q,
            count=len(hits),
            results=[SearchHit(**hit) for hit in hits],
        )

    @app.get("/documents", response_model=list[DocumentSummary])
    def documents(limit: int = Query(100, ge=1, le=500)) -> list[DocumentSummary]:
        conn = connect_readonly(index_path)
        try:
            rows = query_module.list_documents(conn, limit)
        finally:
            conn.close()
        return [DocumentSummary(**row) for row in rows]

    @app.get("/documents/{doc_id}", response_model=DocumentDetail)
    def document(doc_id: str) -> DocumentDetail:
        conn = connect_readonly(index_path)
        try:
            doc = query_module.get_document(conn, doc_id)
        finally:
            conn.close()
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        return DocumentDetail(**doc)

    @app.get("/stats", response_model=Stats)
    def stats() -> Stats:
        conn = connect_readonly(index_path)
        try:
            current = query_module.stats(conn)
        finally:
            conn.close()
        return Stats(**current)

    return app
