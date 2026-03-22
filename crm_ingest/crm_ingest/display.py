"""Rich terminal output helpers."""

from __future__ import annotations

from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from crm_ingest.models import (
    PersonExtract,
    MatchCandidate,
    UpdateProposal,
    InteractionEntry,
)

console = Console()


def section(title: str) -> None:
    console.print(f"\n[bold cyan]── {title} {'─' * max(0, 60 - len(title))}[/bold cyan]")


def success(msg: str) -> None:
    console.print(f"[bold green]✓[/bold green] {msg}")


def warn(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow]  {msg}")


def error(msg: str) -> None:
    console.print(f"[bold red]✗[/bold red] {msg}")


def info(msg: str) -> None:
    console.print(f"[dim]→[/dim] {msg}")


# ── Transcript ─────────────────────────────────────────────────────────────────

def show_transcript(cleaned: str) -> None:
    section("Cleaned Transcript")
    console.print(Panel(cleaned, border_style="dim", padding=(1, 2)))


# ── Extracted person ───────────────────────────────────────────────────────────

def show_extract(extract: PersonExtract) -> None:
    section("Extracted Person Details")

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Field", style="bold", width=28)
    table.add_column("Value")

    def _val(field: str, value: Optional[str | list]) -> str:
        if value is None or value == [] or value == "":
            return "[dim]—[/dim]"
        text = ", ".join(value) if isinstance(value, list) else str(value)
        if field in extract.low_confidence_fields:
            return f"[yellow]{text}  [dim](low confidence)[/dim][/yellow]"
        return text

    rows = [
        ("Full Name", "full_name", extract.full_name),
        ("Company", "company", extract.company),
        ("Role", "role", extract.role),
        ("Where Met", "where_met", extract.where_met),
        ("Date Met", "date_met", extract.date_met),
        ("Location", "location", extract.location),
        ("Mutual Connections", "mutual_connections", extract.mutual_connections),
        ("Interests", "interests", extract.interests),
        ("Follow-Up Tasks", "follow_up_tasks", extract.follow_up_tasks),
        ("Promises Made", "promises_made", extract.promises_made),
        ("Tags", "tags", extract.tags),
    ]

    for label, field, value in rows:
        table.add_row(label, _val(field, value))

    console.print(table)

    console.print(
        Panel(
            extract.narrative_summary,
            title="[bold]Summary[/bold]",
            border_style="blue",
            padding=(1, 2),
        )
    )

    if extract.low_confidence_fields:
        warn(f"Low-confidence fields: {', '.join(extract.low_confidence_fields)}")


# ── Match candidates ───────────────────────────────────────────────────────────

def show_match_candidates(candidates: list[MatchCandidate]) -> None:
    section("Identity Match Candidates")

    if not candidates:
        warn("No match candidates found in the People sheet.")
        return

    table = Table(box=box.SIMPLE_HEAD, show_header=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", style="bold")
    table.add_column("Company")
    table.add_column("Confidence", justify="right")
    table.add_column("Reasoning")

    for i, c in enumerate(candidates, 1):
        pct = f"{c.confidence * 100:.0f}%"
        color = "green" if c.confidence >= 0.8 else "yellow" if c.confidence >= 0.5 else "red"
        table.add_row(
            str(i),
            c.full_name,
            c.company or "—",
            f"[{color}]{pct}[/{color}]",
            c.reasoning,
        )

    console.print(table)


# ── Update proposal ────────────────────────────────────────────────────────────

def show_update_proposal(proposal: UpdateProposal) -> None:
    section("Proposed Updates to People Record")

    console.print(f"[bold]{proposal.summary}[/bold]\n")

    if not proposal.field_updates:
        info("No field updates proposed.")
        return

    table = Table(box=box.SIMPLE_HEAD, show_header=True)
    table.add_column("Field", style="bold", width=28)
    table.add_column("Action", width=14)
    table.add_column("Current Value", style="dim", width=28)
    table.add_column("Proposed Value", width=36)

    for u in proposal.field_updates:
        action_style = {
            "replace": "red",
            "append": "cyan",
            "add_if_empty": "green",
        }.get(u.action, "white")

        table.add_row(
            u.field,
            f"[{action_style}]{u.action}[/{action_style}]",
            (u.current_value or "—")[:60],
            u.proposed_value[:60],
        )

    console.print(table)


# ── Interaction entry ──────────────────────────────────────────────────────────

def show_interaction(entry: InteractionEntry) -> None:
    section("Proposed Interaction Log Entry")

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Field", style="bold", width=20)
    table.add_column("Value")

    table.add_row("Date", entry.interaction_date)
    table.add_row("Context", entry.where_context)
    table.add_row("Sentiment", entry.sentiment)
    table.add_row("Summary", entry.summary)
    if entry.key_takeaways:
        table.add_row("Key Takeaways", "\n".join(f"• {t}" for t in entry.key_takeaways))
    if entry.follow_up_items:
        table.add_row("Follow-Up Items", "\n".join(f"• {t}" for t in entry.follow_up_items))
    if entry.promises_made:
        table.add_row("Promises Made", "\n".join(f"• {t}" for t in entry.promises_made))

    console.print(table)


# ── Thank-you note ─────────────────────────────────────────────────────────────

def show_thanks_note(note: str) -> None:
    section("Draft Thank-You Note")
    console.print(Panel(note, border_style="magenta", padding=(1, 2)))


# ── Final confirmation prompt ──────────────────────────────────────────────────

def prompt_confirm(question: str, default: bool = False) -> bool:
    default_hint = "[Y/n]" if default else "[y/N]"
    console.print(f"\n[bold]{question}[/bold] {default_hint} ", end="")
    answer = input().strip().lower()
    if answer == "":
        return default
    return answer in ("y", "yes")


def prompt_choice(question: str, choices: list[str]) -> int:
    """Present numbered choices; return 0-based index. -1 means none."""
    console.print(f"\n[bold]{question}[/bold]")
    for i, c in enumerate(choices, 1):
        console.print(f"  [cyan]{i}[/cyan]. {c}")
    console.print(f"  [cyan]0[/cyan]. None of the above")
    console.print("Enter number: ", end="")
    raw = input().strip()
    try:
        val = int(raw)
        if val == 0:
            return -1
        if 1 <= val <= len(choices):
            return val - 1
    except ValueError:
        pass
    warn("Invalid choice, treating as none.")
    return -1
