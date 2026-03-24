"""
Find a person's LinkedIn profile URL via a DuckDuckGo search.

Uses site:linkedin.com/in scoped query built from name + company + role.
Returns the first matching linkedin.com/in/... URL, or None.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crm_ingest.models import PersonExtract

log = logging.getLogger(__name__)

_LINKEDIN_RE = re.compile(
    r"https?://(www\.)?linkedin\.com/in/[\w\-]+/?", re.IGNORECASE
)


def find_linkedin_url(extract: "PersonExtract") -> str | None:
    """
    Search DuckDuckGo for the person's LinkedIn profile.
    Returns a linkedin.com/in/... URL or None if not found.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            log.warning("ddgs not installed; skipping LinkedIn lookup.")
            return None

    parts = [extract.full_name]
    if extract.company:
        parts.append(extract.company)
    if extract.role:
        parts.append(extract.role)

    query = " ".join(parts) + " site:linkedin.com/in"
    log.info("LinkedIn search query: %s", query)

    try:
        results = DDGS(timeout=10).text(query, max_results=5)
        for r in results:
            url = r.get("href", "")
            if _LINKEDIN_RE.match(url):
                # Normalise: strip trailing slash and query params
                clean = url.split("?")[0].rstrip("/")
                return clean
    except Exception as exc:
        log.warning("LinkedIn search failed: %s", exc)

    return None
