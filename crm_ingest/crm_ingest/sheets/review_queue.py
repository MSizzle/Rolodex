"""
Google Sheets operations for the Review Queue tab.

The Review Queue stores a record of every proposed action before it is
approved and applied. Useful for audit trail and async review.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from crm_ingest.config import config
from crm_ingest.models import UpdateProposal, InteractionEntry, REVIEW_QUEUE_COLUMNS
from crm_ingest.sheets.client import get_worksheet

log = logging.getLogger(__name__)


def _worksheet():
    return get_worksheet(config.sheet_review_queue)


def ensure_headers() -> None:
    ws = _worksheet()
    existing = ws.row_values(1)
    if not existing or existing[0] != "Review ID":
        ws.update("A1", [REVIEW_QUEUE_COLUMNS])
        log.info("Wrote Review Queue sheet headers.")


def enqueue(
    proposed_action: str,            # "update_existing" | "create_new"
    candidate_person_id: str,
    candidate_person_name: str,
    confidence_score: float,
    proposal: UpdateProposal,
    interaction: InteractionEntry,
    status: str = "pending",          # pending | approved | rejected
) -> str:
    """Add a review item to the queue. Returns the Review ID."""
    ws = _worksheet()
    ensure_headers()

    review_id = "R-" + str(uuid.uuid4())[:8].upper()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Serialize proposals compactly for the cell
    field_updates_text = "\n".join(
        f"[{u.action}] {u.field}: {u.proposed_value[:80]}"
        for u in proposal.field_updates
    )

    interaction_text = (
        f"Date: {interaction.interaction_date}\n"
        f"Context: {interaction.where_context}\n"
        f"Summary: {interaction.summary}\n"
        f"Sentiment: {interaction.sentiment}"
    )

    row = {
        "Review ID": review_id,
        "Proposed Action": proposed_action,
        "Candidate Person ID": candidate_person_id,
        "Candidate Person Name": candidate_person_name,
        "Confidence Score": f"{confidence_score:.2f}",
        "Proposed Field Updates": field_updates_text,
        "Proposed New Interaction": interaction_text,
        "Status": status,
        "Reviewer Notes": "",
        "Created At": now,
    }

    row_data = [row.get(col, "") for col in REVIEW_QUEUE_COLUMNS]
    ws.append_row(row_data, value_input_option="USER_ENTERED")
    log.info("Enqueued review item %s (action=%s)", review_id, proposed_action)
    return review_id
