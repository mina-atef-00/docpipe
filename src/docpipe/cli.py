"""docpipe command line interface (typer)."""

from __future__ import annotations

from pathlib import Path

import typer

from . import __version__
from . import query as query_module
from .hashing import sha256_file
from .indexer import build_index, connect_readonly
from .ingest import ingest_corpus, load_manifest, manifest_sha256, write_manifest
from .parse import load_chunks, parse_manifest, pymupdf_available, write_chunks
from .verify import load_exemptions, verify_index

app = typer.Typer(
    help="Deterministic document ingestion, indexing and retrieval pipeline.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"docpipe {__version__}")
        raise typer.Exit(0)


@app.callback()
def _main(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True),
) -> None:
    """docpipe: deterministic ingestion, indexing and retrieval with a provenance gate."""


@app.command()
def ingest(
    corpus: Path = typer.Argument(..., help="Corpus directory to walk."),
    out: Path = typer.Option(Path("manifest.json"), help="Manifest output path."),
    quarantine_dir: Path = typer.Option(
        Path("quarantine"), help="Directory for quarantined inputs."
    ),
) -> None:
    """Walk a corpus directory, hash each file, dedupe repeats, quarantine unsafe inputs."""
    manifest = ingest_corpus(corpus, quarantine_dir)
    write_manifest(manifest, out)
    summary = manifest["summary"]
    typer.echo(
        f"ingest: {summary['files']} files ({summary['unique']} unique, "
        f"{summary['duplicates']} duplicates), {summary['quarantined']} quarantined, "
        f"{summary['skipped']} skipped"
    )
    if summary["quarantined"]:
        typer.echo(f"ingest: quarantined {summary['quarantined']} input(s) under {quarantine_dir}")
    typer.echo(f"ingest: manifest -> {out}")


@app.command()
def parse(
    manifest: Path = typer.Option(Path("manifest.json"), help="Ingest manifest path."),
    out: Path = typer.Option(Path("chunks.jsonl"), help="Chunks output path."),
    corpus: Path | None = typer.Option(
        None, help="Corpus directory (defaults to the manifest's recorded root)."
    ),
) -> None:
    """Extract text and split it into deterministic chunks."""
    data = load_manifest(manifest)
    root = corpus.resolve() if corpus is not None else Path(data["corpus_root"]).resolve()
    pdf_enabled = pymupdf_available()
    if not pdf_enabled:
        typer.echo("parse: pymupdf not installed; PDF files will be skipped.", err=True)
    chunks, report = parse_manifest(data, root, pdf_enabled=pdf_enabled)
    write_chunks(chunks, out)
    typer.echo(
        f"parse: {report['documents']} documents, {report['chunks']} chunks, "
        f"{report['pdf_ok']} pdf, {report['pdf_skipped']} pdf-skipped, "
        f"{report['empty']} empty -> {out}"
    )
    for error in report["errors"]:
        typer.echo(f"parse: error {error['rel_path']}: {error['error']}", err=True)


@app.command()
def index(
    manifest: Path = typer.Option(Path("manifest.json"), help="Ingest manifest path."),
    chunks: Path = typer.Option(Path("chunks.jsonl"), help="Chunks input path."),
    out: Path = typer.Option(Path("index.sqlite"), help="Index output path."),
) -> None:
    """Build the deterministic SQLite index."""
    data = load_manifest(manifest)
    chunk_list = load_chunks(chunks)
    manifest_sha = manifest_sha256(manifest)
    chunks_sha = sha256_file(chunks)
    counts = build_index(data, chunk_list, manifest_sha, chunks_sha, out)
    typer.echo(
        f"index: {counts['documents']} documents, {counts['chunks']} chunks, "
        f"{counts['terms']} terms -> {out}"
    )


query_app = typer.Typer(help="Query an index.", no_args_is_help=True)
app.add_typer(query_app, name="query")


@query_app.command("search")
def query_search(
    term: str = typer.Argument(..., help="Term to search for."),
    index: Path = typer.Option(Path("index.sqlite"), "--index", help="Index path."),
    limit: int = typer.Option(20, help="Maximum number of hits."),
) -> None:
    """Search for chunks containing a term."""
    conn = connect_readonly(index)
    try:
        hits = query_module.search(conn, term, limit)
    finally:
        conn.close()
    if not hits:
        typer.echo(f"search '{term}': no hits")
        return
    typer.echo(f"search '{term}': {len(hits)} hit(s)")
    for hit in hits:
        typer.echo(f"\n{hit['rel_path']}  chunk {hit['chunk_index']}  (doc {hit['doc_id'][:12]})")
        typer.echo(f"  positions: {hit['positions']}")
        typer.echo(f"  {hit['snippet']}")


@query_app.command("doc")
def query_doc(
    doc_id: str = typer.Argument(..., help="Document id (SHA-256)."),
    index: Path = typer.Option(Path("index.sqlite"), "--index", help="Index path."),
) -> None:
    """Fetch a document by id."""
    conn = connect_readonly(index)
    try:
        doc = query_module.get_document(conn, doc_id)
    finally:
        conn.close()
    if doc is None:
        typer.echo(f"doc {doc_id}: not found", err=True)
        raise typer.Exit(1)
    typer.echo(f"rel_path: {doc['rel_path']}")
    typer.echo(f"kind:     {doc['kind']}")
    typer.echo(f"size:     {doc['size']}")
    typer.echo(f"sha256:   {doc['sha256']}")
    typer.echo(f"chunks:   {len(doc['chunks'])}")
    for chunk in doc["chunks"]:
        typer.echo(f"\n--- chunk {chunk['chunk_index']} ---")
        typer.echo(chunk["text"])


@query_app.command("list")
def query_list(
    index: Path = typer.Option(Path("index.sqlite"), "--index", help="Index path."),
    limit: int = typer.Option(100, help="Maximum number of documents."),
) -> None:
    """List documents in the index."""
    conn = connect_readonly(index)
    try:
        rows = query_module.list_documents(conn, limit)
    finally:
        conn.close()
    typer.echo(f"{len(rows)} document(s)")
    for row in rows:
        typer.echo(
            f"  {row['rel_path']:<48} {row['kind']:<9} {row['size']:>6} bytes  "
            f"{row['chunks']} chunk(s)  {row['doc_id'][:12]}"
        )


@query_app.command("context")
def query_context(
    doc_id: str = typer.Argument(..., help="Document id (SHA-256)."),
    chunk_index: int = typer.Argument(..., help="Chunk index to center on."),
    window: int = typer.Option(1, help="Number of neighbouring chunks on each side."),
    index: Path = typer.Option(Path("index.sqlite"), "--index", help="Index path."),
) -> None:
    """Show a chunk and its neighbours within a document."""
    conn = connect_readonly(index)
    try:
        rows = query_module.chunk_context(conn, doc_id, chunk_index, window)
    finally:
        conn.close()
    if not rows:
        typer.echo(f"no chunks for doc {doc_id} around index {chunk_index}", err=True)
        raise typer.Exit(1)
    for row in rows:
        typer.echo(f"\n--- chunk {row['chunk_index']} ---")
        typer.echo(row["text"])


@query_app.command("stats")
def query_stats(
    index: Path = typer.Option(Path("index.sqlite"), "--index", help="Index path."),
) -> None:
    """Print index statistics."""
    conn = connect_readonly(index)
    try:
        current = query_module.stats(conn)
    finally:
        conn.close()
    typer.echo(f"documents: {current['documents']}")
    typer.echo(f"chunks:    {current['chunks']}")
    typer.echo(f"terms:     {current['terms']}")
    typer.echo(f"run_id:    {current['run_id']}")


@app.command()
def verify(
    corpus: Path = typer.Argument(..., help="Corpus directory to verify against."),
    index: Path = typer.Option(Path("index.sqlite"), "--index", help="Index path."),
    manifest: Path = typer.Option(
        Path("manifest.json"), "--manifest", help="Ingest manifest path."
    ),
    exemptions: Path | None = typer.Option(
        None, "--exemptions", help="JSON file of rel_paths exempt from the disk check."
    ),
) -> None:
    """Verify every index row traces to a real source file and its hash."""
    if not index.exists():
        typer.echo(f"verify: error: index not found: {index}", err=True)
        raise typer.Exit(2)
    if not manifest.exists():
        typer.echo(f"verify: error: manifest not found: {manifest}", err=True)
        raise typer.Exit(2)
    if not corpus.exists():
        typer.echo(f"verify: error: corpus not found: {corpus}", err=True)
        raise typer.Exit(2)

    data = load_manifest(manifest)
    exempt = load_exemptions(exemptions)
    ok, failures, summary = verify_index(index, corpus.resolve(), data, exempt)

    typer.echo(
        f"verify: {summary['documents']} documents, {summary['chunks']} chunks, "
        f"{summary['terms']} terms"
    )
    if exempt:
        typer.echo(
            f"verify: EXEMPT {summary['exempted']} document(s) from the disk check: "
            f"{sorted(exempt)}"
        )
    if ok:
        typer.echo("verify: OK - every index row traces to a source file and its hash")
        raise typer.Exit(0)
    typer.echo(f"verify: FAILED - {len(failures)} provenance failure(s):", err=True)
    for failure in failures:
        typer.echo(f"  - {failure}", err=True)
    raise typer.Exit(1)


@app.command()
def api(
    index: Path = typer.Option(Path("index.sqlite"), "--index", help="Index path."),
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
) -> None:
    """Run the FastAPI service."""
    import uvicorn

    from .api import create_app

    web_app = create_app(index)
    uvicorn.run(web_app, host=host, port=port)
