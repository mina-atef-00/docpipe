# docpipe

docpipe walks a directory of text, markdown and PDF files, hashes every file, splits them into chunks, and builds a SQLite index you can search with term, vector or hybrid queries. The part that makes it worth using is the last step: a `verify` command that checks every row in the index against a real file on disk and its content hash. If any row cannot be traced, `verify` prints the exact failure and exits non-zero. An index that reports success while silently dropping data is worse than one that refuses to build, so docpipe makes the refusal explicit.

![Python](https://img.shields.io/badge/python-3.10%2B-89b4fa)
![License](https://img.shields.io/badge/license-MIT-a6e3a1)
![docpipe](https://img.shields.io/badge/docpipe-0.1.0-cba6f7)

## 60 seconds

Generate the seeded demo corpus, run the full pipeline, and see the gate pass:

![ingest, parse and index](docs/img/pipeline.svg)

The gate. This is the line the whole tool exists to produce:

![verify](docs/img/verify-gate.svg)

And one real search, vector mode over the same index:

![search](docs/img/search.svg)

The demo corpus is a fictional widget platform: API references, specs,
changelogs and runbooks. No real credentials or endpoints. The generator is
committed; the files it emits are not.

## Install

```
git clone <this repo>
cd docpipe
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python 3.10 or newer.

## Commands

The five you actually need:

1. `docpipe ingest corpus` - hash the corpus, dedupe repeats, quarantine
   unsafe inputs, write `manifest.json`.
2. `docpipe parse` - extract text and split into deterministic chunks,
   writing `chunks.jsonl`.
3. `docpipe index` - build `index.sqlite` with terms and vectors.
4. `docpipe verify corpus` - the gate. Recomputes every source hash and
   checks referential integrity. Exit 0 only when every index row traces to
   a source file and its hash. Exemptions for legitimately absent files are
   possible with `--exemptions`, and they print loudly when used.
5. `docpipe query search "<text>"` - the default mode is lexical BM25. Add
   `--mode vector` or `--mode hybrid` for embedding-based retrieval.

The default embedding backend is a deterministic local vectoriser, so the
whole pipeline runs with no network and no API key, and the same input gives
you the same index on any machine. There is also an OpenAI-compatible HTTP
backend, selected explicitly, and it never silently falls back to the local
one. A small read-only HTTP service is available too: `docpipe api --index
index.sqlite` exposes `/health`, `/search`, `/documents` and `/stats`.

For a longer look: `docpipe eval` runs a labelled query set and reports
recall@k, precision at k and MRR, and `docpipe query` also has `doc`,
`list`, `context` and `stats` subcommands. All query paths open the index
read-only; a query can never mutate the database.

## Spec and evidence

The deep technical record lives in `EVIDENCE.md`: the verification
transcript, determinism scope and limits, index schema, search ranking
details, and the construction caveats (including one corpus file whose PDF
hash is not reproducible from the seed). If you want numbers with receipts,
start there.

## Development

```
pytest -q        # tests
ruff check .     # lint
mypy             # type check
```

## License

MIT. See `LICENSE`.
