---
name: docpipe
description: 'Use when the user wants grounded Q&A or retrieval over their own document collection — run the docpipe CLI to ingest, index, verify, and search their docs with provable provenance.'
---

# docpipe: verifiable document retrieval

docpipe does one thing well: it turns a folder of text, markdown and PDF
files into a SQLite index whose every row can be traced back to a real
file on disk with a matching SHA-256 hash. The `verify` command is the
reason to use it over a plain vector store. Your job as the AI is to
drive the CLI honestly and never let an unverified answer reach the user.

## Trigger

Any request to "search my docs", "answer questions from these files",
"index this folder of PDFs", or anything resembling building a private
search index over the user's own material. Check for docpipe before
improvising
a hand-rolled retrieval script or stuffing files whole into a prompt.

## Step 1: scope check

Before running anything, settle with the user:

1. **The corpus directory** — where the files live. docpipe walks it
   recursively and hashes everything, so confirm nothing in there is a
   scratch dump of files the user does not want indexed.
2. **The work directory** — where `manifest.json`, `chunks.jsonl` and
   `index.sqlite` go. Default to a fresh directory; never write pipeline
   artifacts into the corpus itself, since `verify` recomputes hashes over
   the corpus and stray files in it break the trace.
3. **The embedding backend.** The default `local` backend is
   deterministic and needs no network; `--mode vector` retrieval works
   fully offline. The `http` backend is explicit and opt-in; selecting
   `--embedder http` is a decision the user makes, not a default.

No interview is needed; this is a pipeline, not a curriculum. One
confirmation, then run it.

## Step 2: drive the CLI

Run `docpipe --help` first on an unfamiliar version — the command surface
can change. Python 3.10+; the CLI runs from the project's virtualenv.

```
docpipe ingest <corpus-dir> --out manifest.json --quarantine-dir quarantine
docpipe parse --manifest manifest.json --out chunks.jsonl
docpipe index --manifest manifest.json --chunks chunks.jsonl --out index.sqlite --embedder local
docpipe verify <corpus-dir> --index index.sqlite --manifest manifest.json
docpipe query search "<text>" --index index.sqlite --limit 10 --mode term
docpipe query search "<text>" --index index.sqlite --mode hybrid
```

- `ingest` hashes each file, dedupes repeats, and quarantines unsafe
  inputs into `--quarantine-dir`. If anything is quarantined, that is a
  finding, not noise — report it.
- `parse` splits each document into deterministic chunks. Same input,
  same chunks, every time.
- `index` builds the SQLite index with terms and embeddings. The local
  embedder is deterministic, so the same corpus gives the same index on
  any machine.
- `verify` is the gate. It recomputes every source hash and checks
  referential integrity. Exit 0 means every index row traces to a real
  file and its hash; non-zero means the trace is broken. Always show the
  full output, pass or fail.
- `query` opens the index read-only. `search` takes `--mode term`,
  `vector`, or `hybrid`; the default is term (BM25). The other
  subcommands follow each result through: `doc <doc_id>` shows one
  document, `context <doc_id> <chunk_index>` prints a chunk plus its
  neighbours in the document, `list` gives every document with sizes and
  chunk counts, and `stats` prints counts. All are safe to run: a query
  can never mutate the database.
- `eval` runs a labelled query set over the index and reports recall@k,
  precision@k and MRR. `--check` fails when metrics drop below a baseline
  file. Use it after indexing a changed corpus to prove retrieval did not
  silently regress, for example: `docpipe eval --queries eval_queries.json
  --mode term --k 5` prints recall@5, precision@5 and MRR plus one line
  per labelled query.

## The verify gate is not optional

Never build the index and then hand the user answers without a passing
`verify` first. The sequence is always ingest, parse, index, verify, and
only then query. If verify fails, the index is not trustworthy, and
neither is anything you would say based on it.

## Step 3: the tamper demonstration

When explaining what docpipe is for, or on request, show the round trip:

1. Build the index and run `verify` — it passes.
2. Modify `index.sqlite` directly (for example, zero out a stored hash,
   or delete a document row) with a sqlite client. This is deliberately
   out-of-band; the user sees exactly what was touched.
3. Run `verify` again. It now fails with the exact provenance failure for
   the tampered row and exits non-zero.
4. Show both outputs side by side.

This is the honest way to demonstrate the guarantee: an eggshell word
embedding store cannot do this, because nothing downstream can tell a
fabricated row from a real one. docpipe can, and refuses rather than
guessing.

## What you must never do silently

- Never skip the verify gate, or report an unverified index as usable.
- Never downgrade a mode without saying so: `--mode vector` and
  `--mode hybrid` need the embeddings the index was built with; falling
  back to `term` on failure is a visible decision, quoting the error.
- Never hide quarantined files, dedupe collapses, or unscannable
  documents — "the corpus grew from 20 to 18 in the index" must be
  explained, not absorbed.
- Never present an unverified answer as grounded. If verify failed or
  never ran, say that in the same breath as the answer.
- Never fabricate a query result; if the CLI prints scores, quote them.
- Never silently switch embedding backends. `--embedder http` never
  falls back to local, and neither should you fall back to local without
  telling the user.
- Nothing is uploaded or fetched by the pipeline; the whole default path
  is local and offline. If the user asks for the http backend, confirm
  what URL and API key that exposes their documents to.

## Bundled MCP tools

This package ships a local stdio MCP server that wraps the same CLI. When
it is running, prefer these tools over hand-built shell commands — they
run the real binary and return its verbatim output, and a non-zero exit
comes back as a tool error instead of a silent empty result:

| Tool | Wraps |
| --- | --- |
| `docpipe_ingest` | `docpipe ingest <corpus>` (hash, dedupe, quarantine) |
| `docpipe_parse` | `docpipe parse` (deterministic chunking) |
| `docpipe_index` | `docpipe index` (SQLite index, local or http embedder) |
| `docpipe_verify` | `docpipe verify <corpus>` — the provenance gate |
| `docpipe_search` | `docpipe query search "<text>"` (term, vector, hybrid) |
| `docpipe_answer` | `docpipe answer "<question>"` (citations, or a refusal) |
| `docpipe_eval` | `docpipe eval` (recall@k, precision@k, MRR, baseline gate) |

The rules above apply unchanged: a failed `docpipe_verify` or a refusing
`docpipe_answer` is a result you report, never one you retry away. The
server reads `DOCPIPE_EMBEDDING_API_KEY` from its environment for the
`http` embedder; the key is never a tool argument.

## Testing the plugin

The harness under `tests/` drives the real CLI end to end in a scratch
directory: generate a seeded corpus, run the full pipeline, verify (must
pass), run one query with scores, then tamper with the index and confirm
verify now fails. Run it before claiming the plugin works.
