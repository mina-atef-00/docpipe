"""Deterministic seeded corpus generator for docpipe.

Produces a neutral technical corpus (API references, changelogs, spec documents
and runbooks) under a target directory. The generator is committed; the files it
emits are not. Run it with a fixed seed to reproduce the same corpus byte for
byte, which the determinism tests rely on.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

SEED = 20260918


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def _overview() -> str:
    return """\
# Widget Platform

This corpus describes a fictional platform that manages named widgets through a
small HTTP API. It exists to exercise the docpipe pipeline and contains no real
credentials, no real endpoints and no real service data.

The platform has three moving parts: an authentication service that issues
short-lived bearer tokens, a widget service that stores and retrieves widgets by
id, and an asynchronous queue that fans out change notifications to subscribers.
Each part is described by an API reference under `api/`, the behaviour contract
in `specs/`, the release history in `changelogs/` and the operational procedures
in `runbooks/`.
"""


def _widget_api() -> str:
    return """\
# Widget API reference

## Authentication

All widget endpoints require an `Authorization: Bearer <token>` header. Tokens
are issued by the authentication service described in `auth_api.md` and expire
after fifteen minutes. A missing or expired token returns `401 Unauthorized`
with a JSON body containing an `error` code of `token_expired` or
`token_missing`.

## Create a widget

```
POST /widgets
Content-Type: application/json

{"name": "pressure-gauge", "labels": ["sensor", "v2"]}
```

On success the service returns `201 Created` and the widget record, including a
server assigned id and a creation timestamp in UTC. The id is a URL safe random
string, not a sequential counter, so callers cannot enumerate widgets.

## List widgets

```
GET /widgets?limit=50&cursor=<opaque>
```

Returns a page of at most fifty widgets plus an opaque `cursor` for the next
page. Passing the cursor back yields the next page. The last page has no cursor
field. Pages are ordered by id.

## Get a widget

```
GET /widgets/{id}
```

Returns the widget or `404 Not Found` when the id does not exist. The response
shape is identical to the create response.

## Update a widget

```
PATCH /widgets/{id}
Content-Type: application/json

{"labels": ["sensor", "v2", "calibrated"]}
```

Updates only the supplied fields. Sending an empty object is a no-op and
returns `200 OK`. Deleting a label is done by sending the field set to `null`.
"""


def _auth_api() -> str:
    return """\
# Authentication API reference

The authentication service mints short-lived bearer tokens and validates them.
It is stateless: tokens carry their own signature and expiry, so no token store
is required on the server.

## Issue a token

```
POST /tokens
Content-Type: application/json

{"client_id": "widget-cli", "client_secret": "..."}
```

Returns `200 OK` with a `token` and an `expires_in` value in seconds. The
default lifetime is nine hundred seconds. The secret must match the value
registered for the client id, otherwise the service returns `401 Unauthorized`.

## Validate a token

```
POST /tokens/validate

{"token": "..."}
```

Returns `200 OK` with the token claims when the signature verifies and the
token has not expired. Returns `401 Unauthorized` otherwise. Validation never
performs a network call; it is a pure signature and clock check.

## Revoke a client

```
DELETE /clients/{client_id}
```

Invalidates every future token issued to the client by rotating the signing key
counter that the token embeds. Already issued tokens remain valid until expiry
because validation only reads the embedded counter.
"""


def _queue_api() -> str:
    return """\
# Queue API reference

The queue service moves widget change notifications from producers to
subscribers. It guarantees at-least-once delivery and preserves order per
widget id.

## Publish a change

```
POST /topics/widgets

{"widget_id": "w_1234", "event": "updated", "payload": {...}}
```

The service assigns a monotonically increasing sequence number per widget id and
returns `202 Accepted` with that number. A producer retrying the same publish
gets the same sequence number back, which makes publishes idempotent.

## Subscribe

```
POST /subscriptions

{"topic": "widgets", "endpoint": "https://subscriber.example/hook"}
```

Returns a subscription id. The queue delivers events to the endpoint with an
`X-Sequence` header so the subscriber can detect gaps and re-order.

## Ack a delivery

```
DELETE /deliveries/{delivery_id}
```

Removes the delivery from the retry set. Unacked deliveries are retried with
exponential backoff for up to one day before the subscription is paused and the
owner is notified.
"""


def _changelog_2026_08() -> str:
    return """\
# Changelog - August 2026

## 1.4.0

New features and fixes shipped to the widget platform during August.

The widget service gained cursor based pagination on the list endpoint, which
replaces the old offset paging. Offsets stayed supported for one release and
are removed in 1.5.0.

The authentication service now rotates signing keys on a schedule and records
the rotation in an audit log. Token validation reads the key counter from the
token, so rotation does not invalidate in flight tokens.

The queue service added idempotent publish. A producer that retries the same
publish now receives the original sequence number instead of a duplicate event.

Bug fixes include a memory leak in the notification dispatcher and a race that
could drop the last page of a listing under heavy load.
"""


def _changelog_2026_07() -> str:
    return """\
# Changelog - July 2026

## 1.3.2

Patch release with two fixes and no schema changes.

The list endpoint no longer returns widgets that were deleted mid page, which
caused duplicate rows when a consumer combined pages. The deploy runbook now
documents the rollback order for the queue service.

## 1.3.1

Fixes a bug where a widget update with an empty labels array was stored as a
null field instead of an empty list, breaking clients that iterate labels
without a null check.
"""


def _data_model_spec() -> str:
    return """\
# Widget data model specification

## Entities

A widget has a stable id, a human readable name, a list of labels and a set of
versioned fields. The id is assigned once at creation and never reused after
deletion.

## Field rules

The name must be between one and one hundred twenty eight characters and may
contain letters, digits, spaces and hyphens. Labels must match the pattern of
lowercase letters, digits and hyphens, with no leading or trailing hyphen.
Duplicate labels are collapsed on write.

## Versioning

Every update bumps a monotonically increasing revision number stored alongside
the widget. Reads may specify a revision to retrieve a point in time view.
Revisions are never mutated after write.
"""


def _request_flow_spec() -> str:
    return """\
# Request flow specification

A write request travels through three stages before a caller sees a result.

The gateway authenticates the bearer token and forwards the request to the
widget service. The widget service validates the body against the schema,
applies the write, and returns the new revision. In parallel it publishes a
change event to the queue so subscribers learn about the update.

A read request skips the queue entirely. It authenticates, then reads directly
from the widget store, which serves from an in memory cache that is invalidated
on every write.

Failure handling is explicit. If the queue publish fails after the write
committed, the write is not rolled back. The queue is eventually consistent and
the platform treats a missing notification as a gap the subscriber can detect
and repair by re listing.
"""


def _deploy_runbook() -> str:
    return """\
# Deploy runbook

This runbook covers a standard release of the widget platform. Follow the steps
in order and do not skip the smoke test.

## Before you start

Confirm the previous release is healthy in the dashboard and that the queue has
no paused subscriptions. Note the current revision of the data model so you can
roll back if needed.

## Steps

1. Deploy the authentication service first and wait for its health check to go
   green.
2. Deploy the widget service, then run the migration for any schema change.
3. Deploy the queue service last, because it depends on both of the above.
4. Run the smoke test that creates, lists and deletes a widget.

## Rollback

Roll back in the reverse order: queue, then widget, then authentication. A
schema migration that already ran must not be reversed by hand; restore the
database snapshot instead.
"""


def _incident_runbook() -> str:
    return """\
# Incident runbook

Use this runbook when the widget service returns errors or the queue falls
behind. It lists the checks in the order they should be performed.

## Widget service errors

Check the service logs for the error code. A `store_timeout` points at the
database, a `schema_violation` at a bad write, and anything else at the
gateway. Restart only the failing component; a full restart hides the cause.

## Queue backlog

Measure the oldest unacked delivery age. If it is under five minutes the queue
is catching up and needs no action. If it is over one hour, pause the busiest
subscriptions first, drain the retry set, then re enable them one at a time.

## Escalation

Escalate after thirty minutes of unresolved errors. Record the exact error
codes seen, the deploy that preceded them, and the oldest queue sequence number
before handing off.
"""


def _backup_runbook() -> str:
    return """\
# Backup runbook

The widget store and the queue both need backups, on different schedules.

## Widget store

Take a full snapshot nightly and keep thirty days of retention. Store snapshots
in a different region from the primary. Test a restore once a month by
replaying the snapshot into a scratch instance and comparing row counts.

## Queue

The queue needs its sequence numbers backed up, not its payloads. Payloads are
re derived from the widget store on delivery. Back up the per topic sequence
counter hourly.

## Verification

A backup that has not been restored is not a backup. The monthly restore test
is the only proof the snapshots are usable.
"""


def _onboarding_notes() -> str:
    return """\
Onboarding notes

The widget platform is small enough to learn in an afternoon. Start with the
data model spec, then read the request flow, then try the API with a throwaway
token. The changelogs are worth reading when something behaves differently from
the docs.

Three rules that matter in practice. First, ids are not sequential, so never
assume you can guess one. Second, writes are idempotent only at the queue
publish step, not at the widget create step. Third, the smoke test in the
deploy runbook is the release gate; a release is not done until it passes.
"""


def _glossary() -> str:
    return """\
Glossary

Bearer token: a short lived credential passed in the Authorization header.
Cursor: an opaque value used to page through a listing.
Idempotent: an operation that yields the same result when retried.
Sequence number: a per widget counter assigned by the queue.
Revision: a per widget counter assigned by the widget store on each update.
Subscription: a queue delivery target with retry and backoff behaviour.
"""


def _document_links() -> str:
    return """\
# Documents

- [Widget API](api/widget_api.md)
- [Authentication API](api/auth_api.md)
- [Queue API](api/queue_api.md)
- [Data model](specs/data-model.md)
- [Request flow](specs/request-flow.md)
- [Deploy runbook](runbooks/deploy-runbook.md)
- [Incident runbook](runbooks/incident-runbook.md)
- [Backup runbook](runbooks/backup-runbook.md)
"""


def _variants(rng: random.Random) -> dict[str, str]:
    """Seeded numeric fields injected into the changelog summaries."""
    build = rng.randint(1000, 9999)
    duration = rng.randint(30, 600)
    return {
        "build": str(build),
        "duration": str(duration),
    }


def _write_pdf(root: Path) -> bool:
    """Write a small two-page PDF using pymupdf when available. Returns success."""
    try:
        import pymupdf
    except ImportError:
        return False
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Architecture overview", fontsize=16)
    page.insert_text(
        (72, 96),
        "The widget platform is a gateway, a widget service, an authentication "
        "service and a queue service.",
        fontsize=11,
    )
    page = doc.new_page()
    page.insert_text((72, 72), "Data flow", fontsize=14)
    page.insert_text(
        (72, 96),
        "Writes flow through the gateway to the widget service and then to the "
        "queue. Reads flow through the gateway directly to the widget store.",
        fontsize=11,
    )
    doc.save(str(root / "specs" / "architecture.pdf"))
    doc.close()
    return True


def generate_corpus(root: Path, seed: int = SEED) -> dict[str, int]:
    """Generate the corpus under *root* and return per-kind file counts."""
    rng = random.Random(seed)
    variants = _variants(rng)

    if root.exists():
        for child in root.iterdir():
            if child.is_dir():
                import shutil

                shutil.rmtree(child)
            else:
                child.unlink()

    docs: dict[str, str] = {
        "README.md": _overview(),
        "api/widget_api.md": _widget_api(),
        "api/auth_api.md": _auth_api(),
        "api/queue_api.md": _queue_api(),
        "changelogs/CHANGELOG-2026-08.md": _changelog_2026_08(),
        "changelogs/CHANGELOG-2026-07.md": _changelog_2026_07(),
        "specs/data-model.md": _data_model_spec(),
        "specs/request-flow.md": _request_flow_spec(),
        "runbooks/deploy-runbook.md": _deploy_runbook(),
        "runbooks/incident-runbook.md": _incident_runbook(),
        "runbooks/backup-runbook.md": _backup_runbook(),
        "notes/onboarding.txt": _onboarding_notes(),
        "notes/glossary.txt": _glossary(),
        "docs.md": _document_links(),
    }
    # A build summary that uses the seeded numeric fields.
    docs["changelogs/build-summary.txt"] = (
        f"build {variants['build']} passed the smoke test in {variants['duration']} seconds.\n"
    )

    for rel, text in sorted(docs.items()):
        _write(root, rel, text)

    # One deliberate duplicate pair to exercise content-hash dedupe.
    _write(root, "notes/glossary-copy.txt", docs["notes/glossary.txt"])

    pdf_written = _write_pdf(root)

    counts = {
        "documents": len(docs) + 1,  # + the duplicate pair file
        "duplicates": 1,
        "pdf": 1 if pdf_written else 0,
    }
    return counts


def eval_queries() -> list[dict[str, str]]:
    """Return the deterministic labelled query set for retrieval evaluation.

    Each query is a natural-language question paired with the rel_path of the
    document that answers it. The set is fixed (no randomness), so the eval
    baseline is reproducible alongside the corpus.
    """
    return [
        {"query": "How do I create a new widget?", "rel_doc": "api/widget_api.md"},
        {"query": "How are bearer tokens issued and validated?", "rel_doc": "api/auth_api.md"},
        {
            "query": "How does the queue service deliver widget change notifications?",
            "rel_doc": "api/queue_api.md",
        },
        {
            "query": "What are the rules for a widget name and its labels?",
            "rel_doc": "specs/data-model.md",
        },
        {"query": "What happens when a write request arrives?", "rel_doc": "specs/request-flow.md"},
        {
            "query": "In what order do I roll back a deployment?",
            "rel_doc": "runbooks/deploy-runbook.md",
        },
        {
            "query": "What checks should I run when the widget service returns errors?",
            "rel_doc": "runbooks/incident-runbook.md",
        },
        {
            "query": "How often should the widget store be backed up?",
            "rel_doc": "runbooks/backup-runbook.md",
        },
        {
            "query": "What changed in the August 2026 changelog?",
            "rel_doc": "changelogs/CHANGELOG-2026-08.md",
        },
        {
            "query": "What fixes shipped in the July 2026 patch release?",
            "rel_doc": "changelogs/CHANGELOG-2026-07.md",
        },
        {"query": "What does the widget platform manage?", "rel_doc": "README.md"},
        {"query": "What does idempotent mean?", "rel_doc": "notes/glossary-copy.txt"},
        {
            "query": "Where should I start learning the widget platform?",
            "rel_doc": "notes/onboarding.txt",
        },
        {
            "query": "What does the architecture overview describe?",
            "rel_doc": "specs/architecture.pdf",
        },
    ]


def write_eval_queries(out: Path) -> None:
    """Write the labelled query set as JSON."""
    payload = {"queries": eval_queries()}
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the docpipe corpus.")
    parser.add_argument("--root", type=Path, default=Path("corpus"))
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--queries-out", type=Path, default=Path("eval_queries.json"))
    args = parser.parse_args()
    counts = generate_corpus(args.root, args.seed)
    write_eval_queries(args.queries_out)
    print(
        f"make_corpus: {counts['documents']} files ({counts['duplicates']} duplicate, "
        f"{counts['pdf']} pdf) -> {args.root}"
    )
    print(f"make_corpus: {len(eval_queries())} eval queries -> {args.queries_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
