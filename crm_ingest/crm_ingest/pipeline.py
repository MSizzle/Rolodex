"""
Main pipeline — orchestrates all modules with human-in-the-loop checkpoints.

Flow:
  1.  Transcribe audio → raw transcript
  2.  Clean transcript
  3.  Extract person details (Claude)
  4.  Load People from Sheets
  5.  Match against existing records (Claude)
  6.  [HUMAN] Confirm identity or reject match
  7.  Generate update proposal (Claude)
  8.  Generate interaction log entry (Claude)
  9.  Show summary of all proposed changes
  10. [HUMAN] Approve writes
  11. Write to Sheets (People + Interactions + Review Queue)
  12. [OPTIONAL] Draft thank-you note
"""

from __future__ import annotations

import logging
from pathlib import Path

from crm_ingest import display
from crm_ingest.config import config
from crm_ingest.models import PersonRecord, MatchCandidate
from crm_ingest.modules import (
    transcriber,
    cleaner,
    extractor,
    matcher,
    proposer,
    interaction_logger,
    thanks_drafter,
)
from crm_ingest.sheets import people as people_sheet
from crm_ingest.sheets import interactions as interactions_sheet
from crm_ingest.sheets import review_queue as review_sheet

log = logging.getLogger(__name__)


def run(
    audio_path: Path,
    draft_thanks: bool = False,
    thanks_format: str = "email",
    skip_transcription: bool = False,
    transcript_text: str | None = None,
) -> None:
    """Execute the full ingestion pipeline."""
    display.console.rule("[bold blue]CRM Ingest[/bold blue]")
    display.info(f"Processing: [bold]{audio_path.name}[/bold]")

    # ── Step 1: Transcribe ─────────────────────────────────────────────────────
    if skip_transcription and transcript_text:
        raw_transcript = transcript_text
        display.success("Using provided transcript (skipping transcription).")
    else:
        display.info("Transcribing audio…")
        raw_transcript = transcriber.transcribe(audio_path)
        display.success(f"Transcription complete ({len(raw_transcript)} chars).")

    # ── Step 2: Clean transcript ───────────────────────────────────────────────
    display.info("Cleaning transcript…")
    cleaned = cleaner.clean(raw_transcript)
    display.show_transcript(cleaned)

    # ── Step 3: Extract person details ─────────────────────────────────────────
    display.info("Extracting person details…")
    extract = extractor.extract(cleaned)
    display.show_extract(extract)

    # ── Step 4: Load existing people ──────────────────────────────────────────
    display.info("Loading People sheet…")
    try:
        people_sheet.ensure_headers()
        existing_people = people_sheet.load_all()
        display.info(f"Found {len(existing_people)} existing records.")
    except Exception as exc:
        display.warn(f"Could not load People sheet: {exc}")
        existing_people = []

    # ── Step 5: Match ─────────────────────────────────────────────────────────
    display.info("Matching against existing records…")
    match_result = matcher.match(extract, existing_people)
    display.show_match_candidates(match_result.candidates)

    # ── Step 6: Human identity confirmation ───────────────────────────────────
    chosen_record: PersonRecord | None = None
    chosen_candidate: MatchCandidate | None = None
    action: str  # "update_existing" | "create_new" | "abort"

    high_confidence = [
        c for c in match_result.candidates
        if c.confidence >= config.match_confidence_threshold
    ]

    if high_confidence:
        choices = [
            f"{c.full_name} ({c.company or 'no company'}) — {c.confidence * 100:.0f}% match"
            for c in high_confidence
        ]
        choices.append("None of these — create a new record")

        idx = display.prompt_choice(
            "Which existing record does this voice memo refer to?",
            choices,
        )

        if idx == -1 or idx == len(high_confidence):
            action = "create_new"
        else:
            chosen_candidate = high_confidence[idx]
            chosen_record = people_sheet.get_by_id(chosen_candidate.person_id)
            action = "update_existing"
    elif match_result.recommendation == "new" or not match_result.candidates:
        display.info("No confident match found.")
        if display.prompt_confirm("Create a new People record for this person?"):
            action = "create_new"
        else:
            display.warn("Aborting — no action taken.")
            return
    else:
        # Unclear — show low-confidence candidates and ask
        display.warn(
            "Possible matches found but none above confidence threshold "
            f"({config.match_confidence_threshold:.0%})."
        )
        choices = [
            f"{c.full_name} ({c.company or 'no company'}) — {c.confidence * 100:.0f}%"
            for c in match_result.candidates
        ]

        idx = display.prompt_choice(
            "Select a record to update, or create new:",
            choices,
        )

        if idx == -1:
            if display.prompt_confirm("Create a new People record?"):
                action = "create_new"
            else:
                display.warn("Aborting — no action taken.")
                return
        else:
            chosen_candidate = match_result.candidates[idx]
            chosen_record = people_sheet.get_by_id(chosen_candidate.person_id)
            action = "update_existing"

    # ── Step 7: Generate update proposal ──────────────────────────────────────
    display.info("Generating update proposal…")

    if action == "update_existing" and chosen_record:
        proposal = proposer.propose_updates(extract, chosen_record)
        person_id = chosen_record.person_id
        person_name = chosen_record.full_name
        confidence = chosen_candidate.confidence if chosen_candidate else 1.0
    else:
        proposal = proposer.propose_new_record(extract)
        person_id = ""   # will be assigned on create
        person_name = extract.full_name
        confidence = 0.0

    display.show_update_proposal(proposal)

    # ── Step 8: Generate interaction log ──────────────────────────────────────
    display.info("Generating interaction log entry…")
    interaction = interaction_logger.generate_interaction(extract, cleaned)
    display.show_interaction(interaction)

    # ── Step 9: Human approval ────────────────────────────────────────────────
    display.console.print()
    display.console.rule("[bold]Ready to Write[/bold]")
    display.console.print(
        f"\nAction:  [bold]{'Update existing record' if action == 'update_existing' else 'Create new record'}[/bold]"
        f"\nPerson:  [bold]{person_name}[/bold]"
        f"\nChanges: [cyan]{len(proposal.field_updates)} field update(s)[/cyan] + 1 new interaction row"
    )

    if not display.prompt_confirm("\nApprove and write to Google Sheets?"):
        display.warn("Cancelled — nothing written to Sheets.")
        # Still log to review queue for audit trail
        _log_to_review_queue(
            action, person_id, person_name, confidence, proposal, interaction,
            status="rejected"
        )
        return

    # ── Step 10: Write to Sheets ──────────────────────────────────────────────
    display.info("Writing to Sheets…")

    if action == "create_new":
        new_record = _build_new_record(extract)
        new_record = people_sheet.create(new_record)
        person_id = new_record.person_id
        person_name = new_record.full_name
        display.success(f"Created new People record: {person_name} ({person_id})")
    else:
        people_sheet.apply_updates(person_id, proposal)
        display.success(f"Updated People record: {person_name} ({person_id})")

    interaction_id = interactions_sheet.append_interaction(
        person_id=person_id,
        full_name=person_name,
        entry=interaction,
        raw_transcript=raw_transcript,
        cleaned_transcript=cleaned,
        source_file=str(audio_path),
    )
    display.success(f"Logged interaction: {interaction_id}")

    _log_to_review_queue(
        action, person_id, person_name, confidence, proposal, interaction,
        status="approved"
    )

    # ── Step 11: Optional thank-you note ─────────────────────────────────────
    if draft_thanks:
        display.info("Drafting thank-you note…")
        note = thanks_drafter.draft_thanks(extract, cleaned, format=thanks_format)
        display.show_thanks_note(note)

    display.console.rule("[bold green]Done[/bold green]")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_new_record(extract) -> PersonRecord:
    """Build a PersonRecord from a PersonExtract for initial creation."""
    from datetime import date

    today_str = date.today().strftime("%B %-d, %Y")
    confidence_note = (
        "low: " + ", ".join(extract.low_confidence_fields)
        if extract.low_confidence_fields else "high"
    )

    return PersonRecord(
        person_id="",                   # assigned by people_sheet.create()
        full_name=extract.full_name,
        company=extract.company,
        role=extract.role,
        where_met=extract.where_met,
        date_first_met=extract.date_met or today_str,
        last_interaction_date=extract.date_met or today_str,
        location=extract.location,
        mutual_connections=", ".join(extract.mutual_connections) if extract.mutual_connections else None,
        interests=", ".join(extract.interests) if extract.interests else None,
        personal_details=extract.personal_details,
        professional_background=extract.professional_background,
        what_they_care_about=extract.what_they_care_about,
        collaboration_ideas=extract.collaboration_ideas,
        follow_up_tasks="; ".join(extract.follow_up_tasks) if extract.follow_up_tasks else None,
        promised_follow_ups="; ".join(extract.promises_made) if extract.promises_made else None,
        tags=", ".join(extract.tags) if extract.tags else None,
        important_notes=extract.narrative_summary,
        source_confidence=confidence_note,
        birthday=extract.birthday,
    )


def _log_to_review_queue(
    action, person_id, person_name, confidence, proposal, interaction, status
) -> None:
    try:
        review_sheet.ensure_headers()
        review_sheet.enqueue(
            proposed_action=action,
            candidate_person_id=person_id,
            candidate_person_name=person_name,
            confidence_score=confidence,
            proposal=proposal,
            interaction=interaction,
            status=status,
        )
    except Exception as exc:
        log.warning("Failed to write to Review Queue: %s", exc)
