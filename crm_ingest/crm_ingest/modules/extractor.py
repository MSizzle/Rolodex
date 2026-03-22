"""
Module 3: Person Detail Extraction

Uses Claude with tool-use to extract a PersonExtract from a cleaned transcript.
Never invents facts — only extracts what is actually stated.
"""

from __future__ import annotations

import logging
from anthropic import Anthropic
from crm_ingest.config import config
from crm_ingest.models import PersonExtract

log = logging.getLogger(__name__)
_client = Anthropic(api_key=config.anthropic_api_key)

SYSTEM_PROMPT = """\
You are a meticulous CRM data extractor. You read a voice memo transcript
about a person the narrator just met, and extract structured facts.

Golden rules:
1. ONLY extract what is clearly stated. Never invent or infer beyond what is said.
2. If a field is not mentioned, leave it null or empty.
3. For fields where the value is inferred rather than stated explicitly, add the
   field name to low_confidence_fields.
4. The narrative_summary should be 2–4 warm, human sentences describing who
   this person is and what was discussed — as if you were briefing a colleague.
5. The transcript is about ONE person. Extract details only about that person.
6. For date_met: resolve any relative expression ("today", "yesterday", "last Monday",
   "last week on Tuesday") to the actual calendar date using today's date provided
   in the user message. Format as "Month DD, YYYY" (e.g. "March 19, 2026").
   If no date is mentioned, use today's date in that format.
7. For birthday: extract any mention of the person's birthday. Format as "Month DD"
   (e.g. "March 19") unless a year is explicitly mentioned, in which case use
   "Month DD, YYYY". Leave null if not mentioned.
"""

_TOOL = {
    "name": "extract_person",
    "description": "Extract structured person details from a voice memo transcript.",
    "input_schema": PersonExtract.model_json_schema(),
}


def extract(cleaned_transcript: str) -> PersonExtract:
    """Extract structured person details from a cleaned transcript."""
    from datetime import date
    log.info("Extracting person details from transcript")

    today_str = date.today().strftime("%B %-d, %Y")  # e.g. "March 19, 2026"

    response = _client.messages.create(
        model=config.claude_model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "extract_person"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Today's date: {today_str}\n\n"
                    "Extract the person's details from this voice memo transcript.\n\n"
                    f"{cleaned_transcript}"
                ),
            }
        ],
    )

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("Claude did not return a tool_use block for extraction.")

    result = PersonExtract.model_validate(tool_block.input)

    log.info("Extracted: %s (%s)", result.full_name, result.company or "no company")
    if config.debug_claude:
        log.debug("EXTRACTOR OUTPUT:\n%s", result.model_dump_json(indent=2))
    return result
