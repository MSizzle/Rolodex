# crm_ingest

A local CLI agent that turns post-meeting voice memos into structured CRM data stored in Google Sheets.

Record a quick memo after meeting someone → the system transcribes it, extracts the person's details, matches them against your existing contacts, proposes updates, logs the interaction, and optionally drafts a thank-you note — all with your approval before anything is written.

---

## How it works

```
memo.m4a
  │
  ▼  1. Transcribe (OpenAI Whisper)
  ▼  2. Clean transcript (Claude)
  ▼  3. Extract person details (Claude → structured JSON)
  ▼  4. Load People sheet & match against existing contacts (Claude)
  │
  ├─ [YOU] Confirm identity or create new record
  │
  ▼  5. Generate field update proposal (Claude)
  ▼  6. Generate interaction log entry (Claude)
  │
  ├─ [YOU] Approve writes
  │
  ▼  7. Write to People + Interactions + Review Queue (Google Sheets)
  ▼  8. (optional) Draft thank-you note (Claude)
```

Nothing is written to Sheets without your explicit approval at step 6.

---

## Google Sheets structure

The system uses three tabs in a single workbook:

| Tab | Purpose |
|---|---|
| **People** | Master contact table — one row per person |
| **Interactions** | Chronological log — one row per memo/meeting |
| **Review Queue** | Audit trail of every proposed action |

### People columns
`Person ID · Full Name · Company · Role · Where Met · Date First Met · Last Interaction Date · Location · Mutual Connections · Relationship Strength · Interests · Personal Details · Professional Background · What They Care About · Opportunities / Collaboration Ideas · Follow-Up Tasks · Promised Follow-Ups · Tags · Warm Intro Paths · Important Notes · Source Confidence · Created At · Updated At`

### Interactions columns
`Interaction ID · Person ID · Full Name · Interaction Date · Where / Context · Summary · Key Takeaways · Raw Transcript · Cleaned Transcript · Follow-Up Items · Promises Made · Sentiment / Relationship Signal · Source File · Logged At`

### Review Queue columns
`Review ID · Proposed Action · Candidate Person ID · Candidate Person Name · Confidence Score · Proposed Field Updates · Proposed New Interaction · Status · Reviewer Notes · Created At`

---

## Setup

### 1. Clone and install

```bash
git clone <this-repo>
cd crm_ingest
pip install -e .
```

Or with a virtual environment (recommended):

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .
```

### 2. Set up Google Sheets

**a. Create a Google Cloud project**
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Enable the **Google Sheets API** and **Google Drive API**

**b. Create a service account**
1. Go to **IAM & Admin → Service Accounts**
2. Click **Create Service Account**
3. Give it a name (e.g. `crm-ingest`)
4. Click **Create and Continue** → skip role assignment → **Done**
5. Click the service account → **Keys** tab → **Add Key → Create New Key → JSON**
6. Save the downloaded JSON file as `credentials.json` in the project root

**c. Create the Google Sheets workbook**
1. Create a new Google Spreadsheet at [sheets.google.com](https://sheets.google.com)
2. Rename the first tab to **People**
3. Add two more tabs: **Interactions** and **Review Queue**
4. Note the spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_IS_THE_ID`**`/edit`
5. Share the spreadsheet with the service account email
   (found in `credentials.json` → `client_email`) — give it **Editor** access

**d. Run `crm_ingest check-sheets`** to verify the connection and write headers.

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_SPREADSHEET_ID=your_spreadsheet_id

# Optional
TRANSCRIPTION_PROVIDER=openai   # or: local
MATCH_CONFIDENCE_THRESHOLD=0.7
DEBUG_CLAUDE=0
```

---

## Usage

### Basic ingestion

```bash
crm_ingest memo.m4a
```

### With thank-you note draft

```bash
crm_ingest memo.m4a --draft-thanks
```

### Specify thank-you format

```bash
crm_ingest memo.m4a --draft-thanks --thanks-format linkedin
# formats: email (default) | text | linkedin
```

### Use a pre-existing transcript (skip audio transcription)

```bash
crm_ingest --transcript memo.txt
```

### Utility commands

```bash
# Verify Google Sheets connection and write headers
crm_ingest check-sheets

# List all contacts in the People sheet
crm_ingest list-people

# Show help
crm_ingest --help
```

---

## Output during a run

```
── Cleaned Transcript ────────────────────────────────────────────────
  [cleaned memo text]

── Extracted Person Details ──────────────────────────────────────────
  Full Name        Sarah Chen
  Company          Benchmark Capital
  Role             Partner
  Where Met        SaaStr Annual, San Francisco
  ...
  ⚠  Low-confidence fields: date_met

── Identity Match Candidates ─────────────────────────────────────────
  #  Name          Company            Confidence  Reasoning
  1  Sarah Chen    Benchmark Capital  92%         Same name and firm

── Proposed Updates to People Record ────────────────────────────────
  ...

── Proposed Interaction Log Entry ────────────────────────────────────
  ...

── Ready to Write ────────────────────────────────────────────────────
  Action:  Update existing record
  Person:  Sarah Chen
  Changes: 4 field update(s) + 1 new interaction row

Approve and write to Google Sheets? [y/N]
```

---

## Swapping transcription providers

**OpenAI Whisper API** (default, requires `OPENAI_API_KEY`):
```env
TRANSCRIPTION_PROVIDER=openai
```

**Local Whisper** (no API key needed, large model download on first use):
```bash
pip install openai-whisper
```
```env
TRANSCRIPTION_PROVIDER=local
```

To add a new provider, implement a function in [crm_ingest/modules/transcriber.py](crm_ingest/modules/transcriber.py) and add it to the `transcribe()` dispatcher.

---

## Architecture

```
crm_ingest/
├── crm_ingest/
│   ├── cli.py                  # typer CLI entry point
│   ├── pipeline.py             # orchestration + human-in-the-loop
│   ├── config.py               # env var loading
│   ├── models.py               # Pydantic data models + sheet column definitions
│   ├── display.py              # rich terminal output helpers
│   ├── modules/
│   │   ├── transcriber.py      # audio → raw text (OpenAI Whisper / local)
│   │   ├── cleaner.py          # raw text → clean transcript (Claude)
│   │   ├── extractor.py        # transcript → PersonExtract (Claude structured output)
│   │   ├── matcher.py          # PersonExtract × People sheet → MatchResult (Claude)
│   │   ├── proposer.py         # PersonExtract × PersonRecord → UpdateProposal (Claude)
│   │   ├── interaction_logger.py  # transcript → InteractionEntry (Claude)
│   │   └── thanks_drafter.py   # transcript → thank-you note (Claude)
│   └── sheets/
│       ├── client.py           # gspread auth + worksheet access
│       ├── people.py           # People tab CRUD
│       ├── interactions.py     # Interactions tab append
│       └── review_queue.py     # Review Queue tab append
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Extending the system

| What | Where |
|---|---|
| Add a new CRM field | `models.py` → `PEOPLE_COLUMNS` and `PersonRecord` |
| Change extraction behaviour | `modules/extractor.py` → `SYSTEM_PROMPT` |
| Tune match sensitivity | `MATCH_CONFIDENCE_THRESHOLD` in `.env` |
| Add outreach drafting | New module in `modules/`, add `--draft-outreach` flag to `cli.py` |
| Add search/lookup command | New `@app.command()` in `cli.py` |
| Add reminders | Query Interactions for overdue follow-ups; new CLI command |

---

## Requirements

- Python 3.11+
- `ANTHROPIC_API_KEY` — Claude API ([console.anthropic.com](https://console.anthropic.com))
- `OPENAI_API_KEY` — OpenAI Whisper API ([platform.openai.com](https://platform.openai.com))
- Google service account credentials with Sheets + Drive access
- A Google Spreadsheet shared with the service account

---

## Troubleshooting

**`Missing required environment variable: GOOGLE_SPREADSHEET_ID`**
→ Copy `.env.example` to `.env` and fill in your values.

**`Google credentials file not found: credentials.json`**
→ Download your service account JSON key and set `GOOGLE_CREDENTIALS_FILE` to its path.

**`gspread.exceptions.SpreadsheetNotFound`**
→ Check `GOOGLE_SPREADSHEET_ID` and make sure the spreadsheet is shared with the service account email.

**`openai.AuthenticationError`**
→ Check `OPENAI_API_KEY`.

**Low transcription accuracy**
→ Try recording closer to the microphone, or switch to `TRANSCRIPTION_PROVIDER=local` with the `medium` Whisper model.

**Match confidence too aggressive/lenient**
→ Adjust `MATCH_CONFIDENCE_THRESHOLD` (0.0–1.0) in `.env`.
