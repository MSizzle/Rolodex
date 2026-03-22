"""
Module 6: Interaction Log Generation

Generates a structured InteractionEntry from the extracted person data and
cleaned transcript. This becomes a new row in the Interactions sheet.
"""

from __future__ import annotations

import logging
from datetime import date
from anthropic import Anthropic
from crm_ingest.config import config
from crm_ingest.models import PersonExtract, InteractionEntry

log = logging.getLogger(__name__)
_client = Anthropic(api_key=config.anthropic_api_key)

SYSTEM_PROMPT = """\
You are drafting a single interaction log entry for a personal CRM.

Given a voice memo transcript and extracted person details, fill in the
InteractionEntry schema accurately and concisely.

Rules:
- interaction_date: resolve to an absolute date formatted as "Month DD, YYYY" (e.g. "March 19, 2026"). If the transcript mentions a relative date ("Tuesday", "last week", "yesterday", "last Monday"), calculate the exact calendar date relative to today's date (provided below). If no date is mentioned, use today's date in that format.
- summary: 2–3 sentences covering the key points of the conversation.
- key_takeaways: 3–5 memorable facts or themes from the conversation.
- follow_up_items: things the narrator said they would do.
- promises_made: things the other person said they would do.
- sentiment: one of: warm / neutral / cool / energised / cautious.
- Do NOT add anything not mentioned in the transcript or extract.
"""


def generate_interaction(
    extract: PersonExtract,
    cleaned_transcript: str,
) -> InteractionEntry:
    """Generate a new interaction log entry."""
    log.info("Generating interaction log entry for %s", extract.full_name)

    today = date.today().strftime("%B %-d, %Y")  # e.g. "March 19, 2026"
    context = (
        f"Today's date: {today}\n"
        f"Person: {extract.full_name}"
        + (f" at {extract.company}" if extract.company else "")
        + f"\nNarrative summary: {extract.narrative_summary}"
    )

    response = _client.messages.parse(
        model=config.claude_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Context:\n{context}\n\n"
                    f"Cleaned transcript:\n{cleaned_transcript}"
                ),
            }
        ],
        output_format=InteractionEntry,
    )

    entry = response.parsed_output
    if entry is None:
        raise ValueError("Claude returned an unparseable response for interaction log.")

    # Ensure date always has a value
    if not entry.interaction_date:
        entry.interaction_date = date.today().strftime("%B %-d, %Y")

    log.info("Interaction entry generated: %s sentiment", entry.sentiment)
    if config.debug_claude:
        log.debug("INTERACTION LOG OUTPUT:\n%s", entry.model_dump_json(indent=2))
    return entry
