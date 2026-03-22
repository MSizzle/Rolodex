"""
Module 7: Thank-You Note Drafting

Only invoked when the user passes --draft-thanks. Generates a warm,
grounded thank-you note based on the actual meeting details.
"""

from __future__ import annotations

import logging
from anthropic import Anthropic
from crm_ingest.config import config
from crm_ingest.models import PersonExtract

log = logging.getLogger(__name__)
_client = Anthropic(api_key=config.anthropic_api_key)

SYSTEM_PROMPT = """\
You are a thoughtful assistant helping someone write a personal thank-you note
after a meeting or networking event.

Style guidelines:
- Warm and genuine, not sycophantic.
- Concise: 3–5 sentences.
- Grounded in specific details from the meeting (topics, interests, ideas shared).
- First person, as if written by the person who had the meeting.
- End with a clear, friendly next-step or closing.
- Do NOT invent details not present in the context provided.
- The default format is email. Adjust only if the format parameter specifies otherwise.
"""


def draft_thanks(
    extract: PersonExtract,
    cleaned_transcript: str,
    format: str = "email",
) -> str:
    """Generate a thank-you note draft."""
    log.info("Drafting thank-you note for %s (format: %s)", extract.full_name, format)

    context = (
        f"Recipient: {extract.full_name}"
        + (f" ({extract.role} at {extract.company})" if extract.role and extract.company
           else f" at {extract.company}" if extract.company
           else "")
        + f"\n\nMeeting summary: {extract.narrative_summary}"
    )

    format_note = {
        "email": "Write as a brief email.",
        "text": "Write as a casual text message (very short, 1–2 sentences).",
        "linkedin": "Write as a LinkedIn connection message (professional, 2–3 sentences).",
    }.get(format, "Write as a brief email.")

    response = _client.messages.create(
        model=config.claude_model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"{context}\n\n"
                    f"Transcript for reference:\n{cleaned_transcript}\n\n"
                    f"Format: {format_note}"
                ),
            }
        ],
    )

    note = response.content[0].text.strip()
    log.info("Thank-you note drafted (%d chars)", len(note))
    return note
