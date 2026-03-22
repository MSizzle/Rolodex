"""
Google Sheets base client.

Uses a service account for auth. The spreadsheet must be shared with the
service account email (found in credentials.json → client_email).

Setup:
  1. Create a Google Cloud project and enable the Sheets + Drive APIs.
  2. Create a service account and download the JSON key.
  3. Set GOOGLE_CREDENTIALS_FILE to the path of that JSON.
  4. Share your spreadsheet with the service account email.
  5. Set GOOGLE_SPREADSHEET_ID to the spreadsheet's ID.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

import gspread
from google.oauth2.service_account import Credentials

from crm_ingest.config import config

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


@lru_cache(maxsize=1)
def get_spreadsheet() -> gspread.Spreadsheet:
    """Return an authenticated gspread Spreadsheet object (cached)."""
    if config.google_credentials_json:
        creds = Credentials.from_service_account_info(
            json.loads(config.google_credentials_json), scopes=SCOPES
        )
    else:
        creds_path = config.google_credentials_file
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Google credentials file not found: {creds_path}\n"
                "Set GOOGLE_CREDENTIALS_JSON or GOOGLE_CREDENTIALS_FILE in your .env."
            )
        creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(config.google_spreadsheet_id)
    log.info("Connected to spreadsheet: %s", spreadsheet.title)
    return spreadsheet


def get_worksheet(tab_name: str) -> gspread.Worksheet:
    """Return a worksheet by tab name, creating it if it doesn't exist.

    If the cached connection is stale (network error), clears the cache and
    retries once with a fresh connection.
    """
    for attempt in range(2):
        try:
            spreadsheet = get_spreadsheet()
            try:
                ws = spreadsheet.worksheet(tab_name)
                log.debug("Opened worksheet: %s", tab_name)
                return ws
            except gspread.WorksheetNotFound:
                log.warning("Worksheet '%s' not found — creating it.", tab_name)
                ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=30)
                return ws
        except OSError as exc:
            if attempt == 0:
                log.warning("Sheets connection error (%s) — clearing cache and retrying.", exc)
                get_spreadsheet.cache_clear()
            else:
                raise
