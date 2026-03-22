"""
Google Sheets operations for the People tab.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from crm_ingest.config import config
from crm_ingest.models import PersonRecord, PEOPLE_COLUMNS, UpdateProposal
from crm_ingest.sheets.client import get_worksheet

log = logging.getLogger(__name__)


def _worksheet():
    return get_worksheet(config.sheet_people)


def ensure_headers() -> None:
    """Write header row if the sheet is empty."""
    ws = _worksheet()
    existing = ws.row_values(1)
    if not existing or existing[0] != "Person ID":
        ws.update("A1", [PEOPLE_COLUMNS])
        log.info("Wrote People sheet headers.")


def load_all() -> list[PersonRecord]:
    """Return all rows as PersonRecord objects (skips empty rows)."""
    ws = _worksheet()
    records = ws.get_all_records(expected_headers=PEOPLE_COLUMNS)
    people = []
    for row in records:
        pid = str(row.get("Person ID", "")).strip()
        name = str(row.get("Full Name", "")).strip()
        if not pid or not name:
            continue
        people.append(PersonRecord.from_sheet_row({k: str(v) for k, v in row.items()}))
    log.info("Loaded %d people records.", len(people))
    return people


def get_by_id(person_id: str) -> PersonRecord | None:
    """Fetch a single PersonRecord by Person ID."""
    people = load_all()
    for p in people:
        if p.person_id == person_id:
            return p
    return None


def create(record: PersonRecord) -> PersonRecord:
    """Append a new row. Assigns a new UUID if person_id is empty."""
    ws = _worksheet()
    ensure_headers()

    if not record.person_id:
        record = record.model_copy(update={"person_id": _new_id()})

    now = _now()
    record = record.model_copy(update={"created_at": now, "updated_at": now})

    row_data = [record.to_sheet_row().get(col, "") for col in PEOPLE_COLUMNS]
    ws.append_row(row_data, value_input_option="USER_ENTERED")
    log.info("Created People record: %s (%s)", record.full_name, record.person_id)
    return record


def apply_updates(person_id: str, proposal: UpdateProposal) -> None:
    """Apply a set of FieldUpdates to the row matching person_id."""
    ws = _worksheet()
    ensure_headers()

    # Find the row number
    all_values = ws.get_all_values()
    headers = all_values[0]
    row_idx = None
    pid = person_id.strip()
    for i, row in enumerate(all_values[1:], start=2):  # 1-indexed, header is row 1
        if row[0].strip() == pid:
            row_idx = i
            break

    if row_idx is None:
        raise ValueError(f"Person ID '{person_id}' not found in People sheet.")

    # Build current row as dict
    current_row = dict(zip(headers, all_values[row_idx - 1]))

    # Apply each update
    updates: list[tuple[str, str]] = []  # (a1_notation, new_value)

    for upd in proposal.field_updates:
        col_name = upd.field
        if col_name not in headers:
            log.warning("Column '%s' not found in People sheet — skipping.", col_name)
            continue

        col_idx = headers.index(col_name)
        current = current_row.get(col_name, "").strip()

        if upd.action == "replace":
            new_val = upd.proposed_value
        elif upd.action == "append":
            new_val = (current + "\n" + upd.proposed_value).strip() if current else upd.proposed_value
        elif upd.action == "add_if_empty":
            new_val = upd.proposed_value if not current else current
        else:
            new_val = upd.proposed_value

        a1 = gspread_a1(row_idx, col_idx + 1)
        updates.append((a1, new_val))

    # Always update the "Updated At" column
    if "Updated At" in headers:
        col_idx = headers.index("Updated At")
        a1 = gspread_a1(row_idx, col_idx + 1)
        updates.append((a1, _now()))

    # Batch update
    for a1, val in updates:
        ws.update(a1, [[val]])

    log.info("Applied %d field updates to person_id=%s", len(updates) - 1, person_id)


def write_field(person_id: str, column: str, value: str) -> None:
    """Write a single field value for the row matching person_id."""
    ws = _worksheet()
    all_values = ws.get_all_values()
    headers = all_values[0]

    if column not in headers:
        log.warning("Column '%s' not found in People sheet — skipping.", column)
        return

    row_idx = None
    pid = person_id.strip()
    for i, row in enumerate(all_values[1:], start=2):
        if row[0].strip() == pid:
            row_idx = i
            break

    if row_idx is None:
        raise ValueError(f"Person ID '{person_id}' not found in People sheet.")

    col_idx = headers.index(column)
    ws.update(gspread_a1(row_idx, col_idx + 1), [[value]])

    # Also bump Updated At
    if "Updated At" in headers:
        ua_idx = headers.index("Updated At")
        ws.update(gspread_a1(row_idx, ua_idx + 1), [[_now()]])

    log.info("Wrote '%s' for person_id=%s", column, person_id)


def delete_by_id(person_id: str) -> None:
    """Delete the row matching person_id."""
    ws = _worksheet()
    all_values = ws.get_all_values()
    pid = person_id.strip()
    for i, row in enumerate(all_values[1:], start=2):
        if row[0].strip() == pid:
            ws.delete_rows(i)
            log.info("Deleted People row for person_id=%s", person_id)
            return
    raise ValueError(f"Person ID '{person_id}' not found.")


def restore_record(record: PersonRecord) -> None:
    """Overwrite an existing row with the full contents of record."""
    ws = _worksheet()
    all_values = ws.get_all_values()
    headers = all_values[0]
    pid = record.person_id.strip()
    for i, row in enumerate(all_values[1:], start=2):
        if row[0].strip() == pid:
            row_data = [record.to_sheet_row().get(col, "") for col in headers]
            ws.update(f"A{i}", [row_data])
            log.info("Restored People record for person_id=%s", record.person_id)
            return
    raise ValueError(f"Person ID '{record.person_id}' not found for restore.")


def gspread_a1(row: int, col: int) -> str:
    """Convert 1-based row/col to A1 notation (e.g. 2, 3 → C2)."""
    col_str = ""
    while col > 0:
        col, remainder = divmod(col - 1, 26)
        col_str = chr(65 + remainder) + col_str
    return f"{col_str}{row}"


def _new_id() -> str:
    return "P-" + str(uuid.uuid4())[:8].upper()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
