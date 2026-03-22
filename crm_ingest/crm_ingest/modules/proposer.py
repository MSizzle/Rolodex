"""
Module 5: Update Proposal Generation

Given a PersonExtract and an existing PersonRecord, proposes specific
field-level updates: what to replace, what to append, what to fill in.
"""

from __future__ import annotations

import logging
from anthropic import Anthropic
from crm_ingest.config import config
from crm_ingest.models import PersonExtract, PersonRecord, UpdateProposal

log = logging.getLogger(__name__)
_client = Anthropic(api_key=config.anthropic_api_key)

SYSTEM_PROMPT = """\
You are a careful CRM update advisor. You compare fresh information from a
voice memo with an existing contact record and propose field-level updates.

For each proposed change, choose an action:
  - "replace"      → the new value is clearly better/more accurate than current
  - "append"       → the new value adds useful context; keep the old value too
  - "add_if_empty" → only set if the field is currently blank

Guiding principles:
1. Prefer appending over replacing for rich text fields (notes, background, interests).
2. Only propose "replace" when the new info clearly supersedes the old.
3. Do not propose a change if the new value adds nothing new.
4. Do not invent facts not present in the extracted data.
5. Always provide a brief reasoning for each proposed change.
"""

_NEW_RECORD_SYSTEM = """\
You are a CRM data specialist. Given extracted facts from a voice memo,
generate the initial field values for a brand-new contact record.

Rules:
- Only populate fields that have actual data.
- Use action "add_if_empty" for every field (it's a new record).
- Do not invent anything.
"""

_TOOL = {
    "name": "propose_updates",
    "description": "Propose field-level CRM updates based on extracted voice memo data.",
    "input_schema": UpdateProposal.model_json_schema(),
}


def propose_updates(
    extract: PersonExtract,
    existing: PersonRecord,
) -> UpdateProposal:
    """Propose field-level updates to an existing PersonRecord."""
    log.info("Generating update proposal for person_id=%s", existing.person_id)

    response = _client.messages.create(
        model=config.claude_model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "propose_updates"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Existing record:\n{_record_to_text(existing)}\n\n"
                    f"Fresh data from voice memo:\n{_extract_to_text(extract)}\n\n"
                    "Propose field updates."
                ),
            }
        ],
    )

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("Claude did not return a tool_use block for proposals.")

    proposal = UpdateProposal.model_validate(tool_block.input)
    log.info("Proposed %d field updates", len(proposal.field_updates))
    if config.debug_claude:
        log.debug("PROPOSER OUTPUT:\n%s", proposal.model_dump_json(indent=2))
    return proposal


def propose_new_record(extract: PersonExtract) -> UpdateProposal:
    """Generate initial field values for a brand-new contact record."""
    log.info("Generating initial field values for new record: %s", extract.full_name)

    response = _client.messages.create(
        model=config.claude_model,
        max_tokens=4096,
        system=_NEW_RECORD_SYSTEM,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "propose_updates"},
        messages=[
            {
                "role": "user",
                "content": (
                    "Generate initial field values for this new contact:\n\n"
                    + _extract_to_text(extract)
                ),
            }
        ],
    )

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("Claude did not return a tool_use block for new record.")

    proposal = UpdateProposal.model_validate(tool_block.input)
    log.info("Generated %d initial fields", len(proposal.field_updates))
    return proposal


# ── Helpers ────────────────────────────────────────────────────────────────────

def _record_to_text(r: PersonRecord) -> str:
    lines = [f"Person ID: {r.person_id}", f"Full Name: {r.full_name}"]
    for attr, label in [
        ("company", "Company"), ("role", "Role"), ("where_met", "Where Met"),
        ("date_first_met", "Date First Met"), ("location", "Location"),
        ("mutual_connections", "Mutual Connections"), ("interests", "Interests"),
        ("personal_details", "Personal Details"),
        ("professional_background", "Professional Background"),
        ("what_they_care_about", "What They Care About"),
        ("collaboration_ideas", "Collaboration Ideas"),
        ("follow_up_tasks", "Follow-Up Tasks"),
        ("promised_follow_ups", "Promised Follow-Ups"),
        ("tags", "Tags"), ("important_notes", "Important Notes"),
    ]:
        val = getattr(r, attr, None)
        if val:
            lines.append(f"{label}: {val}")
    return "\n".join(lines)


def _extract_to_text(e: PersonExtract) -> str:
    lines = [f"Full Name: {e.full_name}"]
    if e.company:
        lines.append(f"Company: {e.company}")
    if e.role:
        lines.append(f"Role: {e.role}")
    if e.where_met:
        lines.append(f"Where Met: {e.where_met}")
    if e.date_met:
        lines.append(f"Date Met: {e.date_met}")
    if e.location:
        lines.append(f"Location: {e.location}")
    if e.mutual_connections:
        lines.append(f"Mutual Connections: {', '.join(e.mutual_connections)}")
    if e.interests:
        lines.append(f"Interests: {', '.join(e.interests)}")
    if e.personal_details:
        lines.append(f"Personal Details: {e.personal_details}")
    if e.professional_background:
        lines.append(f"Professional Background: {e.professional_background}")
    if e.what_they_care_about:
        lines.append(f"What They Care About: {e.what_they_care_about}")
    if e.collaboration_ideas:
        lines.append(f"Collaboration Ideas: {e.collaboration_ideas}")
    if e.follow_up_tasks:
        lines.append(f"Follow-Up Tasks: {'; '.join(e.follow_up_tasks)}")
    if e.promises_made:
        lines.append(f"Promises Made: {'; '.join(e.promises_made)}")
    if e.tags:
        lines.append(f"Tags: {', '.join(e.tags)}")
    if e.narrative_summary:
        lines.append(f"Summary: {e.narrative_summary}")
    if e.low_confidence_fields:
        lines.append(f"Low-confidence fields: {', '.join(e.low_confidence_fields)}")
    return "\n".join(lines)
