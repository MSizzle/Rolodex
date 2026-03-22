"""Load and validate environment configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (crm_ingest/) regardless of cwd
_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Missing required environment variable: {key}\n"
            f"Copy .env.example to .env and fill in your values."
        )
    return val


class Config:
    # Claude
    anthropic_api_key: str = _require("ANTHROPIC_API_KEY")

    # Transcription
    transcription_provider: str = os.getenv("TRANSCRIPTION_PROVIDER", "openai")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")

    # Google Sheets — use JSON string or file path
    google_credentials_json: str | None = os.getenv("GOOGLE_CREDENTIALS_JSON")
    google_credentials_file: Path = Path(
        os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    )
    google_spreadsheet_id: str = _require("GOOGLE_SPREADSHEET_ID")

    # Behaviour
    match_confidence_threshold: float = float(
        os.getenv("MATCH_CONFIDENCE_THRESHOLD", "0.7")
    )
    debug_claude: bool = os.getenv("DEBUG_CLAUDE", "0") == "1"

    # Sheet tab names (override if your workbook uses different names)
    sheet_people: str = os.getenv("SHEET_PEOPLE", "People")
    sheet_interactions: str = os.getenv("SHEET_INTERACTIONS", "Interactions")
    sheet_review_queue: str = os.getenv("SHEET_REVIEW_QUEUE", "Review Queue")

    # Claude model
    claude_model: str = "claude-opus-4-6"


config = Config()
