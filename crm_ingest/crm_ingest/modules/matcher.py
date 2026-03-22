"""
Module 4: Identity Matching

Compares an extracted PersonExtract against existing People sheet records
and returns ranked match candidates with confidence scores.
"""

from __future__ import annotations

import json
import logging
from anthropic import Anthropic
from crm_ingest.config import config
from crm_ingest.models import PersonExtract, PersonRecord, MatchResult

log = logging.getLogger(__name__)
_client = Anthropic(api_key=config.anthropic_api_key)

SYSTEM_PROMPT = """\
You are an identity resolution expert for a personal CRM.

You will receive:
  A) Details about a person just met (the "new person").
  B) A list of existing contact records.

Your task:
- Find up to 3 existing records that might be the same person.
- Score each candidate from 0.0 (no match) to 1.0 (certain match).
- A score above 0.5 requires BOTH a similar name AND a similar company or role. Neither alone is sufficient.
- Be appropriately skeptical — a common name with no job overlap scores below 0.5.
- Include reasoning for each candidate in one sentence.
- Set recommendation to:
    "match"   → one clear candidate at 0.8 or above
    "unclear" → multiple plausible candidates or one around 0.5–0.79
    "new"     → no candidates or all below 0.5

Never make up person IDs or names not in the existing records.
"""

_TOOL = {
    "name": "match_contacts",
    "description": "Return ranked match candidates for an extracted person against existing contacts.",
    "input_schema": MatchResult.model_json_schema(),
}


def match(
    extract: PersonExtract,
    existing_people: list[PersonRecord],
) -> MatchResult:
    """Return ranked match candidates for the extracted person."""
    log.info(
        "Matching '%s' against %d existing records",
        extract.full_name,
        len(existing_people),
    )

    if not existing_people:
        log.info("No existing people records — recommending new.")
        return MatchResult(candidates=[], recommendation="new")

    compact = [
        {
            "person_id": p.person_id,
            "full_name": p.full_name,
            "company": p.company or "",
            "role": p.role or "",
            "location": p.location or "",
            "where_met": p.where_met or "",
            "tags": p.tags or "",
        }
        for p in existing_people
    ]

    new_person_summary = (
        f"Name: {extract.full_name}\n"
        f"Company: {extract.company or 'unknown'}\n"
        f"Role: {extract.role or 'unknown'}\n"
        f"Where met: {extract.where_met or 'unknown'}\n"
        f"Location: {extract.location or 'unknown'}\n"
        f"Tags: {', '.join(extract.tags) if extract.tags else 'none'}"
    )

    response = _client.messages.create(
        model=config.claude_model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "match_contacts"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"New person to match:\n{new_person_summary}\n\n"
                    f"Existing contacts ({len(compact)} records):\n"
                    f"{json.dumps(compact, indent=2)}"
                ),
            }
        ],
    )

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("Claude did not return a tool_use block for matching.")

    result = MatchResult.model_validate(tool_block.input)
    result.candidates.sort(key=lambda c: c.confidence, reverse=True)

    log.info(
        "Match recommendation: %s (%d candidates)",
        result.recommendation,
        len(result.candidates),
    )
    if config.debug_claude:
        log.debug("MATCHER OUTPUT:\n%s", result.model_dump_json(indent=2))
    return result
