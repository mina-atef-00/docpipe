"""Grounded, extractive answers over retrieved chunks.

``answer`` runs a ranked search, then composes a fully extractive answer from
the top chunks: every sentence it emits is a verbatim sentence from a retrieved
chunk, wrapped in a per-claim citation marker ``[doc_id:chunk]`` (the doc id is
abbreviated to its first 12 hex characters, matching the CLI's display
convention). No text is ever generated, so every citation traces to a chunk
that retrieval actually returned.

Refusal contract: when no retrieved chunk covers at least ``ceil(2/3)`` of the
query's significant tokens, the result is ``{"refused": True, "answer":
"...cannot answer from the corpus...", "citations": []}`` and the CLI exits with
code 1. This mirrors the lexical profile of the shipped index embedder: the
answer layer is grounded in the same profile that the vector side uses to rank
different documents apart, so a question nothing in the corpus talks about is
refused instead of answered with irrelevant citations.

Answer-level eval (``evaluate_answers``) scores citation precision and recall
over a labelled query set: a claim is "relevant" when its source chunk's
document is the labelled expected document.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .chunking import tokenize
from .embed import Embedder
from .search import run_search

_REFUSAL = "cannot answer from the corpus: no retrieved chunk supports the query."
# Interrogatives and light verbs carry no topic signal; they pollute the
# support gate because question prose shares them with every chunk.
_STOPWORDS = frozenset({"what", "when", "where", "which", "does", "have", "this", "that", "with"})


def _short(doc_id: str) -> str:
    return doc_id[:12]


def _significant_tokens(text: str) -> tuple[str, ...]:
    """Stopword-pruned query tokens; the full token list when nothing remains."""
    tokens = [t for t in tokenize(text) if len(t) >= 4 and t not in _STOPWORDS]
    return tuple(dict.fromkeys(tokens if tokens else tokenize(text)))


def _sentences(text: str) -> list[str]:
    """Split chunk text into rough sentences (newline, period or list boundaries)."""
    parts: list[str] = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.lstrip("# ").strip()
        if not paragraph:
            continue
        start = 0
        for i, ch in enumerate(paragraph):
            if ch in ".!?":
                if paragraph[start : i + 1].strip():
                    parts.append(paragraph[start : i + 1].strip())
                start = i + 1
        rest = paragraph[start:].strip()
        if rest:
            parts.append(rest)
    return parts


def answer(
    conn: sqlite3.Connection,
    query: str,
    embedder: Embedder,
    mode: str = "term",
    k: int = 5,
    alpha: float = 0.5,
) -> dict[str, Any]:
    """Compose an extractive, fully cited answer from the top *k* chunks.

    Refuses (``refused: True``, empty citations, answer set to the refusal
    sentence) when no retrieved chunk passes the profile gate above.
    """
    hits = run_search(conn, query, mode, embedder, limit=k, alpha=alpha)
    tokens = _significant_tokens(query)
    for hit in hits:
        row = conn.execute(
            "SELECT text FROM chunks WHERE chunk_id = ?", (hit["chunk_id"],)
        ).fetchone()
        hit["full_text"] = row["text"] if row is not None else hit["snippet"]
    if not hits or not _gate(query, hits):
        return {
            "query": query,
            "refused": True,
            "answer": f"Refusal: {_REFUSAL}",
            "citations": [],
            "hits": [],
        }

    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        text = hit["full_text"]
        marker = f"[{_short(hit['doc_id'])}:{hit['chunk_index']}]"
        for sentence, _matched in _sentence_matches(text, tokens):
            if sentence in seen:
                continue
            seen.add(sentence)
            claims.append(
                {
                    "marker": marker,
                    "text": sentence,
                    "doc_id": hit["doc_id"],
                    "chunk_index": hit["chunk_index"],
                    "chunk_id": hit["chunk_id"],
                    "rel_path": hit["rel_path"],
                }
            )

    lines = [f"{claim['text']} {claim['marker']}" for claim in claims]
    if not lines:
        return {
            "query": query,
            "refused": True,
            "answer": f"Refusal: {_REFUSAL}",
            "citations": [],
            "hits": hits,
        }
    return {
        "query": query,
        "refused": False,
        "answer": "\n".join(lines),
        "citations": claims,
        "hits": hits,
    }


def _gate(query: str, hits: list[dict[str, Any]]) -> bool:
    """True when a top hit covers a supermajority of the query's significant tokens.

    The gate is a share, not an all: natural-language questions never overlap
    chunk prose verbatim, so requiring every token would refuse legitimate
    queries. A chunk carrying at least ``ceil(2/3)`` of the tokens counts as
    support; short queries (where 2/3 rounds up to 1) need every token, which is
    the calibrated balance between refusing junk queries ("phone number",
    "idempotent") and answering questions the corpus genuinely covers.
    """
    tokens = _significant_tokens(query)
    if not tokens:
        return False
    threshold = (2 * len(tokens)) // 3 + 1  # ceil(2/3 * n)
    for hit in hits:
        low = (hit.get("full_text") or hit.get("snippet", "")).lower()
        matched = sum(1 for token in tokens if token in low)
        if matched >= threshold:
            return True
    return False


def _sentence_matches(text: str, tokens: tuple[str, ...]) -> list[tuple[str, int]]:
    """Return ``(sentence, matches)`` pairs for sentences touching any token."""
    pairs: list[tuple[str, int]] = []
    for sentence in _sentences(text):
        low = sentence.lower()
        matched = sum(1 for token in tokens if token in low)
        if matched:
            pairs.append((sentence, matched))
    return pairs


def render_answer(result: dict[str, Any]) -> str:
    """Human-readable rendering for the CLI."""
    if result["refused"]:
        return result["answer"]
    lines = [f"answer '{result['query']}':"]
    lines.append(result["answer"])
    return "\n".join(lines)


def evaluate_answers(
    conn: sqlite3.Connection,
    queries: list[dict[str, str]],
    embedder: Embedder,
    mode: str = "term",
    k: int = 5,
) -> dict[str, Any]:
    """Score citation precision/recall and refusals over a labelled query set.

    For each query answer, a citation is *relevant* when the cited chunk's
    document equals the labelled ``rel_doc``. Citation precision is the share of
    citations that are relevant; citation recall is the share of queries whose
    answer cites at least one chunk from the expected document.
    """
    total = len(queries)
    supported = 0
    total_citations = 0
    relevant_citations = 0
    refusals = 0
    per_query: list[dict[str, Any]] = []
    for query in queries:
        result = answer(conn, query["query"], embedder, mode=mode, k=k)
        if result["refused"]:
            refusals += 1
            found = False
            claims = 0
            relevant = 0
        else:
            claims = len(result["citations"])
            relevant = sum(1 for c in result["citations"] if c["rel_path"] == query["rel_doc"])
            total_citations += claims
            relevant_citations += relevant
            found = relevant > 0
            if found:
                supported += 1
        per_query.append(
            {
                "query": query["query"],
                "rel_doc": query["rel_doc"],
                "refused": result["refused"],
                "claims": claims,
                "relevant": relevant,
                "supported": found,
            }
        )
    precision = relevant_citations / total_citations if total_citations else 0.0
    recall = supported / total if total else 0.0
    return {
        "k": k,
        "mode": mode,
        "n_queries": total,
        "refusals": refusals,
        "citation_precision": round(precision, 6),
        "citation_recall": round(recall, 6),
        "per_query": per_query,
    }


def render_answer_report(metrics: dict[str, Any]) -> str:
    """Human-readable report for ``evaluate_answers`` metrics."""
    lines = [
        f"answer-eval: {metrics['n_queries']} queries, mode={metrics['mode']}, k={metrics['k']}",
        f"refusals:            {metrics['refusals']}",
        f"citation_precision:  {metrics['citation_precision']}",
        f"citation_recall:     {metrics['citation_recall']}",
    ]
    for item in metrics["per_query"]:
        mark = "refusal" if item["refused"] else ("hit " if item["supported"] else "miss")
        lines.append(
            f"  [{mark}] claims={item['claims']:<3} relevant={item['relevant']:<3}"
            f" {item['query']} -> {item['rel_doc']}"
        )
    return "\n".join(lines)
