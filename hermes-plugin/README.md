# docpipe — portable plugin package

A portable Agent Plugins v1 package that wraps the [docpipe](https://github.com/mina-atef-00/docpipe)
CLI: one bundled skill, plus a local stdio MCP server that exposes the CLI's
pipeline as MCP tools. It is not yet published in the Hermes plugin catalog —
it lives in the docpipe repository under `hermes-plugin/`.

## Why the verify gate is the point

docpipe builds a SQLite index over a folder of text, markdown and PDF files and
can trace every index row back to a real file and a SHA-256 hash. `verify`
recomputes those hashes and exits non-zero the moment a row cannot be traced.
This package does not re-implement any of that retrieval: the MCP server shells
out to the real `docpipe` CLI and returns its verbatim output. **A non-zero exit
is returned as an MCP tool error, never as an empty success** — the pipeline
exists to refuse loudly, and a silent failure would defeat it.

## Layout

```
hermes-plugin/
├── plugin.json                 Agent Plugins v1 manifest (package metadata)
├── mcp.json                    one stdio server, no secrets
├── skills/docpipe/SKILL.md     the docpipe skill (offline, loadable as-is)
├── server/docpipe_mcp.py       MCP JSON-RPC server, standard library only
└── README.md                   this file
```

## Prerequisites

- Python 3.10+.
- The `docpipe` CLI available to the interpreter that runs the MCP server.
  Simplest path: install it into the same interpreter — `pip install docpipe` —
  so `python3 -m docpipe` works from wherever the server is launched. Otherwise
  set `DOCPIPE_BIN` to the docpipe executable (for example the venv's
  `.venv/bin/docpipe`) in the environment of the process that starts the server.
  When neither resolves, the server reports the missing CLI as a tool error
  rather than pretending to run.
- `python3` on `PATH`. The package declares no Python dependencies of its own:
  `server/docpipe_mcp.py` speaks MCP over stdio with the standard library only
  (`subprocess` + `json`), so the package installs cleanly with no SDK.

## Tools

| MCP tool | docpipe command | Notes |
| --- | --- | --- |
| `docpipe_ingest` | `docpipe ingest <corpus>` | hashes, dedupes, quarantines unsafe inputs |
| `docpipe_parse` | `docpipe parse` | deterministic chunking |
| `docpipe_index` | `docpipe index` | `local` (offline, deterministic) or `http` embedder |
| `docpipe_verify` | `docpipe verify <corpus>` | provenance gate; failure is a tool error |
| `docpipe_search` | `docpipe query search` | read-only; `term`, `vector` or `hybrid` |
| `docpipe_answer` | `docpipe answer` | extractive answer with `[doc:chunk]` citations, or a refusal |
| `docpipe_eval` | `docpipe eval` | recall@k, precision@k, MRR; optional baseline gate |

Relative paths are resolved against the server's working directory, which
`mcp.json` sets to `${PLUGIN_DATA}` (a per-install writable directory Hermes
creates). That is why a bare `docpipe_ingest` plus `docpipe_index` writes
`manifest.json` and `index.sqlite` into plugin data rather than into the
read-only package directory.

## Credentials

`mcp.json` declares an empty `env` block and the package stores no secrets —
`env` values in a portable package are visible package data, so none belong
there. The `http` embedding backend reads `DOCPIPE_EMBEDDING_API_KEY`,
`DOCPIPE_EMBEDDING_BASE_URL` and `DOCPIPE_EMBEDDING_MODEL` from the environment
the MCP client passes down, and the API key is deliberately not a tool
argument, so it never lands in a transcript. The default path is entirely
local and offline.

## Validating and using it locally

```
hermes plugins validate <repo>/hermes-plugin --json
python3 <repo>/hermes-plugin/server/docpipe_mcp.py   # speak JSON-RPC on stdin
```

Installing a portable package runs the server as a local executable with full
trust — the same trust as any other plugin you install. The skill is loaded
read-only.
