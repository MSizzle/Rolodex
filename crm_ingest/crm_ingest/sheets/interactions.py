"""
Google Sheets operations for the Interactions tab.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from crm_ingest.config import config
from crm_ingest.models import InteractionEntry, INTERACTIONS_COLUMNS
from crm_ingest.sheets.client import get_worksheet

log = logging.getLogger(__name__)


def _worksheet():
    return get_worksheet(config.sheet_interactions)


def ensure_headers() -> None:
    ws = _worksheet()
    existing = ws.row_values(1)
    if not existing or existing[0] != "Interaction ID":
        ws.update("A1", [INTERACTIONS_COLUMNS])
        log.info("Wrote Interactions sheet headers.")


def append_interaction(
    person_id: str,
    full_name: str,
    entry: InteractionEntry,
    raw_transcript: str,
    cleaned_transcript: str,
    source_file: str = "",
) -> str:
    """Append a new interaction row. Returns the new Interaction ID."""
    ws = _worksheet()
    ensure_headers()

    interaction_id = "I-" + str(uuid.uuid4())[:8].upper()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    row = {
        "Interaction ID": interaction_id,
        "Person ID": person_id,
        "Full Name": full_name,
        "Interaction Date": entry.interaction_date,
        "Where / Context": entry.where_context,
        "Summary": entry.summary,
        "Key Takeaways": "\n".join(f"• {t}" for t in entry.key_takeaways),
        "Raw Transcript": raw_transcript,
        "Cleaned Transcript": cleaned_transcript,
        "Follow-Up Items": "\n".join(f"• {t}" for t in entry.follow_up_items),
        "Promises Made": "\n".join(f"• {t}" for t in entry.promises_made),
        "Sentiment / Relationship Signal": entry.sentiment,
        "Source File": source_file,
        "Logged At": now,
    }

    row_data = [row.get(col, "") for col in INTERACTIONS_COLUMNS]
    ws.append_row(row_data, value_input_option="USER_ENTERED")
    log.info("Appended interaction %s for person_id=%s", interaction_id, person_id)
    return interaction_id


def delete_by_id(interaction_id: str) -> None:
    """Delete the row matching interaction_id."""
    ws = _worksheet()
    all_values = ws.get_all_values()
    for i, row in enumerate(all_values[1:], start=2):
        if row[0] == interaction_id:
            ws.delete_rows(i)
            log.info("Deleted interaction %s", interaction_id)
            return
    raise ValueError(f"Interaction ID '{interaction_id}' not found.")
