"""
CLI entry point.

Usage:
  crm_ingest memo.m4a
  crm_ingest memo.m4a --draft-thanks
  crm_ingest memo.m4a --draft-thanks --thanks-format linkedin
  crm_ingest --transcript memo.txt   # skip audio transcription
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.logging import RichHandler

app = typer.Typer(
    name="crm_ingest",
    help="Ingest a post-meeting voice memo into your Google Sheets CRM.",
    add_completion=False,
)

# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


# ── Main command ──────────────────────────────────────────────────────────────

@app.command()
def ingest(
    audio_file: Optional[Path] = typer.Argument(
        None,
        help="Path to the voice memo audio file (.m4a, .mp3, .wav, etc.).",
        exists=False,  # we validate manually for a cleaner error message
    ),
    draft_thanks: bool = typer.Option(
        False,
        "--draft-thanks",
        "-t",
        help="Also draft a thank-you note after processing.",
    ),
    thanks_format: str = typer.Option(
        "email",
        "--thanks-format",
        "-f",
        help="Format for the thank-you note: email | text | linkedin.",
    ),
    transcript: Optional[Path] = typer.Option(
        None,
        "--transcript",
        help="Provide a plain-text transcript file instead of an audio file.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug logging.",
    ),
) -> None:
    """
    Process a voice memo and ingest it into your Google Sheets CRM.

    Requires a .env file with ANTHROPIC_API_KEY, OPENAI_API_KEY,
    GOOGLE_CREDENTIALS_FILE, and GOOGLE_SPREADSHEET_ID.
    """
    _setup_logging(verbose)

    # Validate inputs
    if transcript and audio_file:
        typer.echo("Error: provide either an audio file or --transcript, not both.", err=True)
        raise typer.Exit(1)

    if not transcript and not audio_file:
        typer.echo("Error: provide an audio file or --transcript.", err=True)
        raise typer.Exit(1)

    transcript_text: str | None = None
    audio_path: Path

    if transcript:
        if not transcript.exists():
            typer.echo(f"Error: transcript file not found: {transcript}", err=True)
            raise typer.Exit(1)
        transcript_text = transcript.read_text(encoding="utf-8")
        # Use the transcript path as a stand-in for audio_path
        audio_path = transcript
    else:
        audio_path = audio_file  # type: ignore[assignment]
        if not audio_path.exists():
            typer.echo(f"Error: audio file not found: {audio_path}", err=True)
            raise typer.Exit(1)

    # Run pipeline
    from crm_ingest import pipeline

    try:
        pipeline.run(
            audio_path=audio_path,
            draft_thanks=draft_thanks,
            thanks_format=thanks_format,
            skip_transcription=transcript_text is not None,
            transcript_text=transcript_text,
        )
    except EnvironmentError as exc:
        typer.echo(f"\n[Config Error] {exc}", err=True)
        raise typer.Exit(1)
    except FileNotFoundError as exc:
        typer.echo(f"\n[File Error] {exc}", err=True)
        raise typer.Exit(1)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130)
    except Exception as exc:
        if verbose:
            raise
        typer.echo(f"\n[Error] {exc}", err=True)
        typer.echo("Run with --verbose for full traceback.", err=True)
        raise typer.Exit(1)


# ── Utility commands ──────────────────────────────────────────────────────────

@app.command()
def check_sheets() -> None:
    """Verify Google Sheets connection and ensure tab headers exist."""
    _setup_logging(False)
    from crm_ingest.sheets import people as ps, interactions as ints, review_queue as rq
    from crm_ingest.display import console, success, error

    console.print("[bold]Checking Google Sheets connection…[/bold]")
    try:
        ps.ensure_headers()
        success(f"People tab OK")
        ints.ensure_headers()
        success(f"Interactions tab OK")
        rq.ensure_headers()
        success(f"Review Queue tab OK")
        console.print("\n[bold green]All tabs ready.[/bold green]")
    except Exception as exc:
        error(f"Sheets connection failed: {exc}")
        raise typer.Exit(1)


@app.command()
def list_people() -> None:
    """Print a summary of all contacts in the People sheet."""
    _setup_logging(False)
    from crm_ingest.sheets import people as ps
    from crm_ingest.display import console

    people = ps.load_all()
    if not people:
        console.print("[dim]No contacts found.[/dim]")
        return

    from rich.table import Table
    from rich import box as rbox

    table = Table(box=rbox.SIMPLE_HEAD, show_header=True)
    table.add_column("ID", style="dim", width=12)
    table.add_column("Name", style="bold")
    table.add_column("Company")
    table.add_column("Role")
    table.add_column("Last Interaction")

    for p in people:
        table.add_row(
            p.person_id,
            p.full_name,
            p.company or "—",
            p.role or "—",
            p.last_interaction_date or "—",
        )

    console.print(table)
    console.print(f"\n[dim]{len(people)} contacts total[/dim]")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app()


if __name__ == "__main__":
    main()
