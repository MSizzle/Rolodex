"""
Module 2: Transcript Cleaning

Takes raw Whisper output (which may include filler words, repetitions, and
run-on sentences) and returns a clean, readable transcript.
"""

from __future__ import annotations

import logging
from anthropic import Anthropic
from crm_ingest.config import config

log = logging.getLogger(__name__)
_client = Anthropic(api_key=config.anthropic_api_key)

SYSTEM_PROMPT = """\
You are a professional transcript editor. You receive raw voice-memo transcripts
and return a clean, readable version.

Rules:
- Remove filler words (um, uh, like, you know) and false starts.
- Fix obvious punctuation and capitalisation.
- Break into natural paragraphs.
- Do NOT add, invent, or reorder any facts.
- Do NOT summarise — keep everything that was said, just cleaned.
- Return only the cleaned transcript text. No preamble or explanation.
"""


def clean(raw_transcript: str) -> str:
    """Return a cleaned version of the raw transcript."""
    log.info("Cleaning transcript (%d chars)", len(raw_transcript))

    response = _client.messages.create(
        model=config.claude_model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Clean this voice memo transcript:\n\n{raw_transcript}",
            }
        ],
    )

    cleaned = response.content[0].text.strip()
    log.info("Cleaned transcript (%d chars)", len(cleaned))
    if config.debug_claude:
        log.debug("CLEANER OUTPUT:\n%s", cleaned)
    return cleaned
