# Evidence

Every claim in the README and DRAFT_PACKAGE.md was run on this machine and its
output is pasted here verbatim. Commands were run from the repository root with
the project virtualenv active (`.venv/bin/activate`). Python is 3.13.14.

Tool versions (pinned in `pyproject.toml`): pytest 9.1.1, ruff 0.16.8,
mypy 2.3.1, typer 0.27.2, fastapi 0.141.1, pydantic 2.13.5.

## 1. CLI entrypoint

```
$ docpipe --version
docpipe 0.1.0

$ docpipe verify --help
Usage: docpipe verify [OPTIONS] {corpus}

 Verify every index row traces to a real source file and its hash.

Arguments
*    corpus      <path>  Corpus directory to verify against. [required]

Options
--index             <path>  Index path. [default: index.sqlite]
--manifest          <path>  Ingest manifest path. [default: manifest.json]
--exemptions        <path>  JSON file of rel_paths exempt from the disk check.
--help                      Show this message and exit.
```

Note: `verify` takes a `corpus` positional argument. It is not optional.

## 2. Corpus generation (seeded)

```
$ python tools/make_corpus.py --root corpus --seed 20260918
make_corpus: 16 files (1 duplicate, 1 pdf) -> corpus

$ find corpus -type f | sort
corpus/api/auth_api.md
corpus/api/queue_api.md
corpus/api/widget_api.md
corpus/changelogs/build-summary.txt
corpus/changelogs/CHANGELOG-2026-07.md
corpus/changelogs/CHANGELOG-2026-08.md
corpus/docs.md
corpus/notes/glossary-copy.txt
corpus/notes/glossary.txt
corpus/notes/onboarding.txt
corpus/README.md
corpus/runbooks/backup-runbook.md
corpus/runbooks/deploy-runbook.md
corpus/runbooks/incident-runbook.md
corpus/specs/architecture.pdf
corpus/specs/data-model.md
corpus/specs/request-flow.md
```

The generator's own "16 files" summary counts its 15 prose documents plus the
one deliberate duplicate pair file. The manifest below counts all 17 files on
disk, of which 16 are unique content hashes.

## 3. Ingest, parse, index

```
$ docpipe ingest corpus --out manifest.json --quarantine-dir quarantine
ingest: 17 files (16 unique, 1 duplicates), 0 quarantined, 0 skipped
ingest: manifest -> manifest.json

$ docpipe parse --manifest manifest.json --out chunks.jsonl --corpus corpus
parse: 16 documents, 22 chunks, 1 pdf, 0 pdf-skipped, 0 empty -> chunks.jsonl

$ docpipe index --manifest manifest.json --chunks chunks.jsonl --out index.sqlite
index: 16 documents, 22 chunks, 1746 terms -> index.sqlite
```

## 4. Verify on a good index (exit 0) and determinism

```
$ docpipe verify corpus --index index.sqlite --manifest manifest.json
verify: 16 documents, 22 chunks, 1746 terms
verify: OK - every index row traces to a source file and its hash
EXIT=0
```

Determinism check: build the index twice from the same manifest and chunk
stream, then hash the SQLite `.dump` of each. Matching hashes prove the build is
byte identical.

```
$ docpipe index --manifest manifest.json --chunks chunks.jsonl --out /tmp/docpipe_a.sqlite
index: 16 documents, 22 chunks, 1746 terms -> /tmp/docpipe_a.sqlite
$ docpipe index --manifest manifest.json --chunks chunks.jsonl --out /tmp/docpipe_b.sqlite
index: 16 documents, 22 chunks, 1746 terms -> /tmp/docpipe_b.sqlite
$ python - <<'EOF'
import sqlite3, hashlib
for p in ['/tmp/docpipe_a.sqlite','/tmp/docpipe_b.sqlite']:
    c = sqlite3.connect(p)
    dump = '\n'.join(l for l in c.iterdump())
    c.close()
    print(p, hashlib.sha256(dump.encode()).hexdigest())
EOF
/tmp/docpipe_a.sqlite ba7364962c827b9de52125a76887cf390f965b90b945855586cbecb8aee1ae57
/tmp/docpipe_b.sqlite ba7364962c827b9de52125a76887cf390f965b90b945855586cbecb8aee1ae57
```

## 5. Deliberate corruption, then verify (exit non-zero)

The index's stored `sha256` for `README.md` is overwritten to simulate someone
tampering with the index after the fact.

```
$ python - <<'EOF'
import sqlite3
c = sqlite3.connect('index.sqlite')
c.execute("UPDATE documents SET sha256 = '0'*64 WHERE rel_path = 'README.md'")
c.commit(); c.close()
print('tampered README.md stored sha256')
EOF
tampered README.md stored sha256

$ docpipe verify corpus --index index.sqlite --manifest manifest.json
verify: 16 documents, 22 chunks, 1746 terms
verify: FAILED - 2 provenance failure(s):
  - document README.md: doc_id 6e702aec0595b7d28d7304c3367239509a3ec113630686defc25955996cc6fd3 != stored sha256 0
  - document README.md: hash mismatch (stored 0, disk 6e702aec0595b7d28d7304c3367239509a3ec113630686defc25955996cc6fd3)
EXIT=1
```

The exit code is 1. The index was then rebuilt clean for the remaining evidence
below.

## 6. Query examples

```
$ docpipe query stats --index index.sqlite
documents: 16
chunks:    22
terms:     1746
run_id:    774613d335ae2cfe6b121836d5fdea8b7059a3d10ebf39acdf97e4c227cd856c

$ docpipe query search widget --index index.sqlite --limit 5
search 'widget': 5 hit(s)

README.md  chunk 0  (doc 6e702aec0595)
  positions: [0, 53]
  # Widget Platform

This corpus describes a fictional platform that m...

api/auth_api.md  chunk 0  (doc 7c5fd260c427)
  positions: [44]
  ...POST /tokens
Content-Type: application/json

{"client_id": "widget-cli", "client_secret": "..."}
```

Returns `200 OK` with a ...

api/queue_api.md  chunk 0  (doc 9b3a3f8c225e)
  positions: [7, 24, 32, 48]
  # Queue API reference

The queue service moves widget change notifications from producers to
subscribers. It guar...

api/widget_api.md  chunk 0  (doc 9daba2ff6d9e)
  positions: [0, 5, 54, 76]
  # Widget API reference

## Authentication

All widget endpoints requ...

api/widget_api.md  chunk 1  (doc 9daba2ff6d9e)
  positions: [38, 44, 66]
  Returns a page of at most fifty widgets plus an opaque `cursor` for the next
page. Passing the cur...
```

Document fetch:

```
$ docpipe query doc 9daba2ff6d9eb7094f0e2600ae8bb6b6c14af9ddb9a4d850a9e6a5e84e2a140c --index index.sqlite
rel_path: api/widget_api.md
kind:     markdown
size:     1421
sha256:   9daba2ff6d9eb7094f0e2600ae8bb6b6c14af9ddb9a4d850a9e6a5e84e2a140c
chunks:   2

--- chunk 0 ---
# Widget API reference

## Authentication

All widget endpoints require an `Authorization: Bearer *** header. Tokens
are issued by the authentication service described in `auth_api.md` and expire
after fifteen minutes. A missing or expired token returns `401 Unauthorized`
with a JSON body containing an `error` code of `token_expired` or
`token_missing`.
...
```

List and chunk context:

```
$ docpipe query list --index index.sqlite --limit 8
8 document(s)
  README.md                                        markdown     651 bytes  1 chunk(s)  6e702aec0595
  api/auth_api.md                                  markdown    1129 bytes  2 chunk(s)  7c5fd260c427
  api/queue_api.md                                 markdown    1018 bytes  2 chunk(s)  9b3a3f8c225e
  api/widget_api.md                                markdown    1421 bytes  2 chunk(s)  9daba2ff6d9e
  changelogs/CHANGELOG-2026-07.md                  markdown     479 bytes  1 chunk(s)  370b456a4ea0
  changelogs/CHANGELOG-2026-08.md                  markdown     792 bytes  1 chunk(s)  7d10a82921cd
  changelogs/build-summary.txt                     text          49 bytes  1 chunk(s)  c639cf1b1b4b
  docs.md                                          markdown     340 bytes  1 chunk(s)  5ede48b47df0

$ docpipe query context 9daba2ff6d9eb7094f0e2600ae8bb6b6c14af9ddb9a4d850a9e6a5e84e2a140c 1 --window 1 --index index.sqlite

--- chunk 0 ---
# Widget API reference
...
--- chunk 1 ---
Returns a page of at most fifty widgets plus an opaque `cursor` for the next
page. Passing the cursor back yields the next page. The last page has no cursor
field. Pages are ordered by id.
...
```

(Chunk text is truncated above with `...` where the full block was already shown
in the document fetch.)

## 7. Tests, lint, type check

```
$ python -m pytest -q
...................................                                      [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is
  deprecated; install `httpx2` instead.
.venv/lib/python3.13/site-packages/starlette/testclient.py:53
  DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use
  anyio.from_thread.BlockingPortal instead.
35 passed, 2 warnings in 0.31s
EXIT=0

$ python -m ruff check .
All checks passed!
EXIT=0

$ python -m ruff format --check .
21 files already formatted
EXIT=0

$ python -m mypy
Success: no issues found in 14 source files
EXIT=0
```

The two pytest warnings are upstream deprecation notices from the Starlette test
client, not from docpipe code.

## 8. FastAPI service

```
$ docpipe api --index index.sqlite --host 127.0.0.1 --port 8765
INFO:     Uvicorn running on http://127.0.0.1:8765 (Press CTRL+C to quit)
INFO:     Application startup complete.

$ curl -s http://127.0.0.1:8765/health
{"status":"ok","index":"index.sqlite","documents":16,"chunks":22}

$ curl -s "http://127.0.0.1:8765/search?q=token&limit=2"
{"query":"token","count":2,"results":[{"doc_id":"7c5fd260c427fa7c676b40760127fbf691a30bf129ecc683309c31ae407ec29e","rel_path":"api/auth_api.md","chunk_index":0,"snippet":"...erence\n\nThe authentication service mints short-lived bearer tokens and validates them.\nIt is stateless: tokens carry their ow...","positions":[26,35,53,87,91]},{"doc_id":"7c5fd260c427fa7c676b40760127fbf691a30bf129ecc683309c31ae407ec29e","rel_path":"api/auth_api.md","chunk_index":1,"snippet":"Returns `200 OK` with the token claims when the signature verifies and the\ntoken has not ex...","positions":[5,13,45,58]}]}

$ curl -s http://127.0.0.1:8765/stats
{"documents":16,"chunks":22,"terms":1746,"run_id":"774613d335ae2cfe6b121836d5fdea8b7059a3d10ebf39acdf97e4c227cd856c"}
```
