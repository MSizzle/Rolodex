"""Pydantic models for all data structures in the pipeline."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ── Extraction output ──────────────────────────────────────────────────────────

class PersonExtract(BaseModel):
    """Structured facts extracted from a cleaned transcript."""

    full_name: str = Field(description="Best guess at the person's full name.")
    company: Optional[str] = Field(None, description="Employer or organisation.")
    role: Optional[str] = Field(None, description="Job title or role.")
    where_met: Optional[str] = Field(None, description="Event, venue, or context.")
    date_met: Optional[str] = Field(None, description="Absolute date in 'Month DD, YYYY' format (e.g. 'March 19, 2026'). Resolve relative expressions using today's date. Null only if truly unmentioned.")
    location: Optional[str] = Field(None, description="City / region.")
    mutual_connections: list[str] = Field(default_factory=list, description="Names of shared contacts mentioned.")
    interests: list[str] = Field(default_factory=list, description="Personal or professional interests mentioned.")
    personal_details: Optional[str] = Field(None, description="Family, hobbies, life context — preserve nuance.")
    professional_background: Optional[str] = Field(None, description="Career history, expertise areas.")
    what_they_care_about: Optional[str] = Field(None, description="Motivations, priorities, values expressed.")
    collaboration_ideas: Optional[str] = Field(None, description="Potential projects, intros, or partnerships.")
    follow_up_tasks: list[str] = Field(default_factory=list, description="Things I said I would do.")
    promises_made: list[str] = Field(default_factory=list, description="Things they said they would do.")
    tags: list[str] = Field(default_factory=list, description="Short labels, e.g. investor, advisor, friend.")
    birthday: Optional[str] = Field(None, description="Birthday in 'Month DD' format (e.g. 'March 19') or 'Month DD, YYYY' if year is mentioned. Null if not mentioned.")
    narrative_summary: str = Field(description="2–4 sentence human-readable summary of who this person is and what we discussed.")
    low_confidence_fields: list[str] = Field(
        default_factory=list,
        description="Names of fields where the value is inferred/uncertain, not clearly stated."
    )


# ── Identity matching ──────────────────────────────────────────────────────────

class MatchCandidate(BaseModel):
    person_id: str
    full_name: str
    company: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0, description="0–1 match confidence.")
    reasoning: str = Field(description="One-sentence explanation of why this is or isn't a match.")


class MatchResult(BaseModel):
    candidates: list[MatchCandidate] = Field(
        description="Up to 3 candidates, ordered by confidence descending."
    )
    recommendation: str = Field(
        description="One of: 'match' (strong existing record), 'new' (no good match), 'unclear' (human should decide)."
    )


# ── Update proposal ────────────────────────────────────────────────────────────

class FieldUpdate(BaseModel):
    field: str = Field(description="Column name in the People sheet.")
    current_value: Optional[str] = Field(None, description="Existing cell value, or null if empty.")
    proposed_value: str = Field(description="Proposed new value.")
    action: str = Field(
        description="One of: 'replace' (overwrite), 'append' (add to existing), 'add_if_empty' (only set if blank)."
    )
    reasoning: str = Field(description="Why this change is warranted.")


class UpdateProposal(BaseModel):
    field_updates: list[FieldUpdate]
    summary: str = Field(description="One-sentence plain-English summary of the proposed changes.")


# ── Interaction log entry ──────────────────────────────────────────────────────

class InteractionEntry(BaseModel):
    interaction_date: str = Field(description="Date of the meeting in 'Month DD, YYYY' format (e.g. 'March 19, 2026').")
    where_context: str = Field(description="Where / how we met (event, coffee, call, etc.).")
    summary: str = Field(description="2–3 sentence summary of the conversation.")
    key_takeaways: list[str] = Field(default_factory=list, description="3–5 bullet points worth remembering.")
    follow_up_items: list[str] = Field(default_factory=list, description="Action items for me.")
    promises_made: list[str] = Field(default_factory=list, description="Commitments they made.")
    sentiment: str = Field(description="Relationship signal: warm / neutral / cool / energised / cautious.")


# ── People sheet record ────────────────────────────────────────────────────────

PEOPLE_COLUMNS = [
    "Person ID",
    "Full Name",
    "Company",
    "Role",
    "Where Met",
    "Date First Met",
    "Last Interaction Date",
    "Location",
    "Mutual Connections",
    "Relationship Strength",
    "Interests",
    "Personal Details",
    "Professional Background",
    "What They Care About",
    "Opportunities / Collaboration Ideas",
    "Follow-Up Tasks",
    "Promised Follow-Ups",
    "Tags",
    "Warm Intro Paths",
    "Important Notes",
    "Source Confidence",
    "Contact Method",
    "LinkedIn URL",
    "Birthday",
    "Created At",
    "Updated At",
]

INTERACTIONS_COLUMNS = [
    "Interaction ID",
    "Person ID",
    "Full Name",
    "Interaction Date",
    "Where / Context",
    "Summary",
    "Key Takeaways",
    "Raw Transcript",
    "Cleaned Transcript",
    "Follow-Up Items",
    "Promises Made",
    "Sentiment / Relationship Signal",
    "Source File",
    "Logged At",
]

REVIEW_QUEUE_COLUMNS = [
    "Review ID",
    "Proposed Action",
    "Candidate Person ID",
    "Candidate Person Name",
    "Confidence Score",
    "Proposed Field Updates",
    "Proposed New Interaction",
    "Status",
    "Reviewer Notes",
    "Created At",
]


class PersonRecord(BaseModel):
    """A row from the People sheet, keyed by column name."""

    person_id: str
    full_name: str
    company: Optional[str] = None
    role: Optional[str] = None
    where_met: Optional[str] = None
    date_first_met: Optional[str] = None
    last_interaction_date: Optional[str] = None
    location: Optional[str] = None
    mutual_connections: Optional[str] = None
    relationship_strength: Optional[str] = None
    interests: Optional[str] = None
    personal_details: Optional[str] = None
    professional_background: Optional[str] = None
    what_they_care_about: Optional[str] = None
    collaboration_ideas: Optional[str] = None
    follow_up_tasks: Optional[str] = None
    promised_follow_ups: Optional[str] = None
    tags: Optional[str] = None
    warm_intro_paths: Optional[str] = None
    important_notes: Optional[str] = None
    source_confidence: Optional[str] = None
    contact_method: Optional[str] = None
    linkedin_url: Optional[str] = None
    birthday: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_sheet_row(cls, row: dict) -> "PersonRecord":
        """Build from a gspread dict_record (column header → value)."""
        def g(col: str) -> Optional[str]:
            v = row.get(col, "").strip()
            return v if v else None

        return cls(
            person_id=g("Person ID") or "",
            full_name=g("Full Name") or "",
            company=g("Company"),
            role=g("Role"),
            where_met=g("Where Met"),
            date_first_met=g("Date First Met"),
            last_interaction_date=g("Last Interaction Date"),
            location=g("Location"),
            mutual_connections=g("Mutual Connections"),
            relationship_strength=g("Relationship Strength"),
            interests=g("Interests"),
            personal_details=g("Personal Details"),
            professional_background=g("Professional Background"),
            what_they_care_about=g("What They Care About"),
            collaboration_ideas=g("Opportunities / Collaboration Ideas"),
            follow_up_tasks=g("Follow-Up Tasks"),
            promised_follow_ups=g("Promised Follow-Ups"),
            tags=g("Tags"),
            warm_intro_paths=g("Warm Intro Paths"),
            important_notes=g("Important Notes"),
            source_confidence=g("Source Confidence"),
            contact_method=g("Contact Method"),
            linkedin_url=g("LinkedIn URL"),
            birthday=g("Birthday"),
            created_at=g("Created At"),
            updated_at=g("Updated At"),
        )

    def to_sheet_row(self) -> dict:
        """Return a column→value dict for gspread writes."""
        return {
            "Person ID": self.person_id,
            "Full Name": self.full_name,
            "Company": self.company or "",
            "Role": self.role or "",
            "Where Met": self.where_met or "",
            "Date First Met": self.date_first_met or "",
            "Last Interaction Date": self.last_interaction_date or "",
            "Location": self.location or "",
            "Mutual Connections": self.mutual_connections or "",
            "Relationship Strength": self.relationship_strength or "",
            "Interests": self.interests or "",
            "Personal Details": self.personal_details or "",
            "Professional Background": self.professional_background or "",
            "What They Care About": self.what_they_care_about or "",
            "Opportunities / Collaboration Ideas": self.collaboration_ideas or "",
            "Follow-Up Tasks": self.follow_up_tasks or "",
            "Promised Follow-Ups": self.promised_follow_ups or "",
            "Tags": self.tags or "",
            "Warm Intro Paths": self.warm_intro_paths or "",
            "Important Notes": self.important_notes or "",
            "Source Confidence": self.source_confidence or "",
            "Contact Method": self.contact_method or "",
            "LinkedIn URL": self.linkedin_url or "",
            "Birthday": self.birthday or "",
            "Created At": self.created_at or "",
            "Updated At": self.updated_at or "",
        }
