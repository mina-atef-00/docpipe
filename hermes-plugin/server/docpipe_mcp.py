#!/usr/bin/env python3
"""Minimal stdio MCP server exposing the docpipe CLI as MCP tools.

No third-party dependency: the JSON-RPC 2.0 handshake, ``tools/list`` and
``tools/call`` are implemented here against the MCP stdio transport
(newline-delimited JSON messages on stdin/stdout).

Every tool shells out to the real ``docpipe`` command line and returns its
verbatim stdout/stderr. A non-zero exit is surfaced as an MCP tool error
(``isError: true``) with the process output and exit code attached -- docpipe
exists to refuse loudly, so nothing here may swallow a failure.

Prerequisite: docpipe must be importable by the interpreter running this
script (``pip install docpipe``), or ``DOCPIPE_BIN`` must point at the CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "docpipe", "version": "0.1.0"}
DEFAULT_TIMEOUT = 900


class ToolError(Exception):
    """A tool could not run at all (missing CLI, bad arguments, timeout)."""


# ── locating the CLI ─────────────────────────────────────────────────────────


def docpipe_argv() -> List[str]:
    """Resolve the docpipe CLI invocation.

    Preference order: an explicit ``DOCPIPE_BIN``, then ``docpipe`` as a module
    of the interpreter running this server, then ``docpipe`` on ``PATH``.
    """
    override = os.environ.get("DOCPIPE_BIN", "").strip()
    if override:
        return [override]
    try:
        import docpipe  # noqa: F401
    except ImportError:
        pass
    else:
        return [sys.executable, "-m", "docpipe"]
    found = shutil.which("docpipe")
    if found:
        return [found]
    raise ToolError(
        "docpipe CLI not found. Install it into the interpreter that runs this "
        f"server ({sys.executable}): pip install docpipe -- or set DOCPIPE_BIN "
        "to the docpipe executable."
    )


def run_cli(argv: List[str], timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run docpipe with *argv*, returning combined output; raise ToolError on failure."""
    command = docpipe_argv() + argv
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError(f"cannot execute {command[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"docpipe {argv[0]} timed out after {timeout:g}s") from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ToolError(
            f"docpipe {argv[0]} exited {proc.returncode} (command: {' '.join(command)})\n"
            f"{output.strip() or '<no output>'}"
        )
    return output.strip() or f"docpipe {argv[0]}: exit 0 (no output)"


# ── tool definitions ─────────────────────────────────────────────────────────

TEXT = {"type": "string"}


def _arg_builder(**specs) -> Dict[str, Any]:
    """Build (properties, required) from simple keyword specs."""
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for name, spec in specs.items():
        prop = dict(spec)
        if prop.pop("_required", False):
            required.append(name)
        properties[name] = prop
    return {"type": "object", "properties": properties, "required": required}


def _paths(values: Dict[str, Any], spec: List[tuple]) -> List[str]:
    """Append ``--flag value`` pairs for the options the caller actually supplied."""
    argv: List[str] = []
    for key, flag in spec:
        value = values.get(key)
        if value is not None and value != "":
            argv += [flag, str(value)]
    return argv


def tool_ingest(values: Dict[str, Any]) -> str:
    corpus = values.get("corpus")
    if not corpus:
        raise ToolError("'corpus' is required")
    return run_cli(["ingest", str(corpus)] + _paths(values, [
        ("out", "--out"), ("quarantine_dir", "--quarantine-dir")]))


def tool_parse(values: Dict[str, Any]) -> str:
    return run_cli(["parse"] + _paths(values, [
        ("manifest", "--manifest"), ("out", "--out"), ("corpus", "--corpus")]))


def tool_index(values: Dict[str, Any]) -> str:
    return run_cli(["index"] + _paths(values, [
        ("manifest", "--manifest"), ("chunks", "--chunks"), ("out", "--out"),
        ("embedder", "--embedder"), ("embedding_base_url", "--embedding-base-url"),
        ("embedding_model", "--embedding-model")]))


def tool_verify(values: Dict[str, Any]) -> str:
    corpus = values.get("corpus")
    if not corpus:
        raise ToolError("'corpus' is required: verify recomputes hashes over the corpus")
    return run_cli(["verify", str(corpus)] + _paths(values, [
        ("index", "--index"), ("manifest", "--manifest"), ("exemptions", "--exemptions")]))


def tool_search(values: Dict[str, Any]) -> str:
    query = values.get("query")
    if not query:
        raise ToolError("'query' is required")
    return run_cli(["query", "search", str(query)] + _paths(values, [
        ("index", "--index"), ("limit", "--limit"), ("mode", "--mode")]))


def tool_answer(values: Dict[str, Any]) -> str:
    question = values.get("question")
    if not question:
        raise ToolError("'question' is required")
    return run_cli(["answer", str(question)] + _paths(values, [
        ("index", "--index"), ("k", "--k"), ("mode", "--mode")]))


def tool_eval(values: Dict[str, Any]) -> str:
    argv = ["eval"] + _paths(values, [
        ("index", "--index"), ("queries", "--queries"), ("k", "--k"), ("mode", "--mode"),
        ("baseline", "--baseline")])
    if values.get("check"):
        argv.append("--check")
    return run_cli(argv)


MODE = {"type": "string", "enum": ["term", "vector", "hybrid"],
        "description": "Ranking mode. term = BM25 (offline); vector/hybrid need the "
                       "embeddings the index was built with."}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "docpipe_ingest",
        "description": "Walk a corpus directory, hash every file, dedupe repeats and "
                       "quarantine unsafe inputs. Reports how many files were quarantined "
                       "or skipped -- report those, never absorb them.",
        "inputSchema": _arg_builder(
            corpus={"type": "string", "description": "Corpus directory to walk.", "_required": True},
            out={"type": "string", "description": "Manifest output path (default manifest.json in the plugin data dir)."},
            quarantine_dir={"type": "string", "description": "Directory for quarantined inputs (default quarantine/)."},
        ),
        "run": tool_ingest,
    },
    {
        "name": "docpipe_parse",
        "description": "Extract text and split the ingest manifest into deterministic chunks "
                       "(same input, same chunks).",
        "inputSchema": _arg_builder(
            manifest=TEXT,
            out=TEXT,
            corpus={"type": "string", "description": "Corpus directory; defaults to the manifest's recorded root."},
        ),
        "run": tool_parse,
    },
    {
        "name": "docpipe_index",
        "description": "Build the deterministic SQLite index with terms and chunk embeddings. "
                       "The default 'local' embedder is offline and deterministic; 'http' is "
                       "explicit opt-in and requires DOCPIPE_EMBEDDING_API_KEY in the environment.",
        "inputSchema": _arg_builder(
            manifest=TEXT,
            chunks=TEXT,
            out=TEXT,
            embedder={"type": "string", "enum": ["local", "http"],
                      "description": "Embedding backend. Never switches silently."},
            embedding_base_url={"type": "string", "description": "HTTP embedding base URL (embedder=http)."},
            embedding_model={"type": "string", "description": "HTTP embedding model name (embedder=http)."},
        ),
        "run": tool_index,
    },
    {
        "name": "docpipe_verify",
        "description": "The provenance gate: recompute every source hash and check referential "
                       "integrity. Exit 0 means every index row traces to a real file and its "
                       "hash; a non-zero exit comes back as a tool error listing each failure. "
                       "Run this before answering anything from the index.",
        "inputSchema": _arg_builder(
            corpus={"type": "string", "description": "Corpus directory to verify against.", "_required": True},
            index=TEXT,
            manifest=TEXT,
            exemptions={"type": "string", "description": "JSON file of rel_paths exempt from the disk check."},
        ),
        "run": tool_verify,
    },
    {
        "name": "docpipe_search",
        "description": "Ranked search across the index. Read-only: a query can never mutate the "
                       "database. Quote the real scores the CLI prints.",
        "inputSchema": _arg_builder(
            query={"type": "string", "description": "Query text.", "_required": True},
            index=TEXT,
            mode=MODE,
            limit={"type": "integer", "description": "Maximum number of hits (default 20)."},
        ),
        "run": tool_search,
    },
    {
        "name": "docpipe_answer",
        "description": "Compose an extractive answer with [doc:chunk] citations from the corpus. "
                       "Refuses (exit 1, surfaced as a tool error) when no retrieved chunk "
                       "overlaps the question -- pass that refusal through verbatim.",
        "inputSchema": _arg_builder(
            question={"type": "string", "description": "Question to answer from the corpus.", "_required": True},
            index=TEXT,
            mode=MODE,
            k={"type": "integer", "description": "Number of chunks to ground the answer in (default 5)."},
        ),
        "run": tool_answer,
    },
    {
        "name": "docpipe_eval",
        "description": "Evaluate ranked retrieval: recall@k, precision@k and MRR over a labelled "
                       "query set. With check=true it exits non-zero when metrics fall below the "
                       "baseline -- a retrieval regression is a tool error, not a footnote.",
        "inputSchema": _arg_builder(
            index=TEXT,
            queries=TEXT,
            k={"type": "integer", "description": "Rank cut-off for recall and precision (default 5)."},
            mode=MODE,
            check={"type": "boolean", "description": "Fail when metrics drop below the baseline file."},
            baseline=TEXT,
        ),
        "run": tool_eval,
    },
]

TOOLS_BY_NAME: Dict[str, Callable[[Dict[str, Any]], str]] = {
    tool["name"]: tool["run"] for tool in TOOLS
}


def list_tools() -> List[Dict[str, Any]]:
    """Public tool descriptors (the callable is server-side only)."""
    return [{"name": tool["name"], "description": tool["description"],
             "inputSchema": tool["inputSchema"]} for tool in TOOLS]


# ── JSON-RPC over stdio ──────────────────────────────────────────────────────


def _result(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _call_tool(params: Dict[str, Any], request_id: Any) -> Dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(request_id, -32602, "tools/call 'arguments' must be an object")
    handler = TOOLS_BY_NAME.get(str(name))
    if handler is None:
        return _error(request_id, -32602, f"unknown tool {name!r}; known tools: "
                                          f"{', '.join(sorted(TOOLS_BY_NAME))}")
    try:
        text = handler(arguments)
    except ToolError as exc:
        # Never swallow: the failure text and exit code reach the client.
        return _result(request_id, {"content": [{"type": "text", "text": str(exc)}],
                                    "isError": True})
    except Exception as exc:  # pragma: no cover - defensive
        return _result(request_id, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                                    "isError": True})
    return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": False})


def handle(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch one JSON-RPC message; None means "no response" (notification)."""
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    if request_id is None:
        return None  # notification (notifications/initialized and friends)
    if not isinstance(params, dict):
        return _error(request_id, -32602, "'params' must be an object")
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(request_id, {
            "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": list_tools()})
    if method == "tools/call":
        return _call_tool(params, request_id)
    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return _result(request_id, {key: []})
    return _error(request_id, -32601, f"method not found: {method}")


def serve(stdin=None, stdout=None) -> int:
    """Read newline-delimited JSON-RPC from *stdin*, write responses to *stdout*."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response: Optional[Dict[str, Any]] = _error(None, -32700, f"parse error: {exc}")
        else:
            if not isinstance(message, dict):
                response = _error(None, -32600, "invalid request: expected a JSON object")
            else:
                response = handle(message)
        if response is None:
            continue
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
