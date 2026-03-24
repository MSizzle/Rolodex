"""
Telegram bot for voice memo → CRM ingestion.

Flow:
  1. User sends a voice/audio message
  2. Bot downloads audio, transcribes with Whisper, cleans & extracts via Claude
  3. Bot shows match candidates → inline buttons for identity choice
  4. Bot generates proposal + interaction log → inline buttons for approve/reject
  5. On approval → writes to Google Sheets, replies with summary

Run:
  source .venv/bin/activate
  TELEGRAM_BOT_TOKEN=<token> python telegram_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Always load .env from the same directory as this script
_here = Path(__file__).parent
load_dotenv(_here / ".env")

# Add the package root to sys.path so crm_ingest is importable without editable install
sys.path.insert(0, str(_here))

from crm_ingest.config import config
from crm_ingest.models import PersonRecord, MatchCandidate, UpdateProposal, InteractionEntry
from crm_ingest.modules import (
    cleaner,
    extractor,
    matcher,
    proposer,
    interaction_logger,
    linkedin_finder,
)
from crm_ingest.sheets import people as people_sheet
from crm_ingest.sheets import interactions as interactions_sheet
from crm_ingest.sheets import review_queue as review_sheet
from crm_ingest import pipeline as _pipeline_module  # for _build_new_record / _log_to_review_queue

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
WAITING_IDENTITY = 1
WAITING_APPROVAL = 2
WAITING_EDIT = 3
WAITING_CONTACT_METHOD = 4

# Thread pool for running sync pipeline functions without blocking the event loop
_executor = ThreadPoolExecutor(max_workers=4)

# Per-chat session state
sessions: dict[int, dict] = {}

# ── Undo state ────────────────────────────────────────────────────────────────
import json as _json

_UNDO_FILE = _here / "undo_state.json"


def _save_undo(state: dict) -> None:
    _UNDO_FILE.write_text(_json.dumps(state))


def _load_undo() -> dict | None:
    if _UNDO_FILE.exists():
        try:
            return _json.loads(_UNDO_FILE.read_text())
        except Exception:
            pass
    return None


def _clear_undo() -> None:
    _UNDO_FILE.unlink(missing_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run_sync(fn, *args):
    """Run a synchronous function in the thread executor."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_executor, fn, *args)


def _truncate(text: str, limit: int = 3000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _format_proposal(proposal) -> str:
    if not proposal.field_updates:
        return "_No field updates proposed._"
    lines = [f"*{proposal.summary}*\n"]
    for u in proposal.field_updates:
        action_emoji = {"replace": "🔄", "append": "➕", "add_if_empty": "🆕"}.get(u.action, "•")
        current = (u.current_value or "—")[:80]
        proposed = u.proposed_value[:80]
        lines.append(f"{action_emoji} *{u.field}*\n  _was:_ {current}\n  _now:_ {proposed}")
    return "\n\n".join(lines)


def _format_interaction(entry) -> str:
    parts = [
        f"*Date:* {entry.interaction_date}",
        f"*Context:* {entry.where_context}",
        f"*Sentiment:* {entry.sentiment}",
        f"*Summary:* {entry.summary}",
    ]
    if entry.key_takeaways:
        parts.append("*Key Takeaways:*\n" + "\n".join(f"• {t}" for t in entry.key_takeaways))
    if entry.follow_up_items:
        parts.append("*Follow-Up Items:*\n" + "\n".join(f"• {t}" for t in entry.follow_up_items))
    return "\n".join(parts)


# ── Step 1: Receive transcript text ───────────────────────────────────────────

async def handle_transcript(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    msg = update.effective_message
    raw_transcript = msg.text.strip()

    if not raw_transcript:
        await msg.reply_text("Please send your transcript as a text message.")
        return ConversationHandler.END

    await msg.reply_text(f"Got it! Processing ({len(raw_transcript)} chars)...")

    try:
        loop = asyncio.get_running_loop()
        cleaned = raw_transcript  # text messages are already clean

        await msg.reply_text("⏳ Step 1/3: Extracting person details (Claude)...")
        extract = await loop.run_in_executor(
            _executor, extractor.extract, cleaned
        )
        await msg.reply_text(f"✅ Extracted: {extract.full_name or 'Unknown'}.")

        await msg.reply_text("⏳ Step 2/3: Loading contacts from Google Sheets...")
        existing_people = await loop.run_in_executor(
            _executor, people_sheet.load_all
        )
        await msg.reply_text(f"✅ Loaded {len(existing_people)} contacts.")

        await msg.reply_text("⏳ Step 3/3: Matching (Claude)...")
        match_result = await loop.run_in_executor(
            _executor, matcher.match, extract, existing_people
        )
        await msg.reply_text("✅ Match complete.")

    except Exception as exc:
        log.exception("Pipeline error")
        await msg.reply_text(f"Error during processing: {exc}")
        return ConversationHandler.END

    # Save session state
    sessions[chat_id] = {
        "audio_path": None,
        "raw_transcript": raw_transcript,
        "cleaned": cleaned,
        "extract": extract,
        "existing_people": existing_people,
        "match_result": match_result,
    }

    # ── Show transcript summary ────────────────────────────────────────────────
    summary_text = (
        f"*Transcript (cleaned):*\n{_truncate(cleaned, 800)}\n\n"
        f"*Extracted:* {extract.full_name or 'Unknown'}"
        + (f" @ {extract.company}" if extract.company else "")
        + (f", {extract.role}" if extract.role else "")
    )
    await msg.reply_text(_truncate(summary_text), parse_mode=ParseMode.MARKDOWN)

    # ── Build identity choice buttons ─────────────────────────────────────────
    all_candidates = [
        c for c in match_result.candidates
        if c.confidence >= config.match_confidence_threshold
    ]

    if all_candidates:
        keyboard = []
        for i, c in enumerate(all_candidates):
            label = f"{c.full_name} ({c.company or 'no company'}) — {c.confidence * 100:.0f}%"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"identity:{i}")])
        keyboard.append([InlineKeyboardButton("➕ Create new record", callback_data="identity:new")])

        await msg.reply_text(
            "*Who does this memo refer to?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN,
        )
        sessions[chat_id]["candidates"] = all_candidates
        return WAITING_IDENTITY

    else:
        # No candidates at all → go straight to create new
        await msg.reply_text(
            "No existing contacts found. Creating a new record...",
        )
        sessions[chat_id]["action"] = "create_new"
        sessions[chat_id]["chosen_record"] = None
        sessions[chat_id]["chosen_candidate"] = None
        return await _generate_proposal_and_ask(update, context, chat_id)


# ── Step 2: Identity choice ────────────────────────────────────────────────────

async def handle_identity_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    session = sessions.get(chat_id)

    if not session:
        await query.edit_message_text("Session expired. Please send a new voice message.")
        return ConversationHandler.END

    data = query.data  # "identity:0", "identity:1", ..., "identity:new"
    choice = data.split(":", 1)[1]

    if choice == "new":
        session["action"] = "create_new"
        session["chosen_record"] = None
        session["chosen_candidate"] = None
        await query.edit_message_text("Creating a new record...")
    else:
        idx = int(choice)
        candidates = session["candidates"]
        chosen_candidate = candidates[idx]
        chosen_record = await asyncio.get_running_loop().run_in_executor(
            _executor, people_sheet.get_by_id, chosen_candidate.person_id
        )
        session["action"] = "update_existing"
        session["chosen_candidate"] = chosen_candidate
        session["chosen_record"] = chosen_record
        await query.edit_message_text(
            f"Updating record for *{chosen_candidate.full_name}*...",
            parse_mode=ParseMode.MARKDOWN,
        )

    return await _generate_proposal_and_ask(update, context, chat_id)


async def _generate_proposal_and_ask(
    update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> int:
    session = sessions[chat_id]
    extract = session["extract"]
    action = session["action"]
    chosen_record = session.get("chosen_record")
    chosen_candidate = session.get("chosen_candidate")
    msg = update.effective_message or update.callback_query.message

    await msg.reply_text("Generating update proposal...")

    try:
        if action == "update_existing" and chosen_record:
            proposal = await asyncio.get_running_loop().run_in_executor(
                _executor, proposer.propose_updates, extract, chosen_record
            )
            person_id = chosen_record.person_id
            person_name = chosen_record.full_name
            confidence = chosen_candidate.confidence if chosen_candidate else 1.0
        else:
            proposal = await asyncio.get_running_loop().run_in_executor(
                _executor, proposer.propose_new_record, extract
            )
            person_id = ""
            person_name = extract.full_name
            confidence = 0.0

        interaction = await asyncio.get_running_loop().run_in_executor(
            _executor,
            interaction_logger.generate_interaction,
            extract,
            session["cleaned"],
        )

        # LinkedIn search — inject as add_if_empty field update if found
        await msg.reply_text("🔍 Searching for LinkedIn profile...")
        try:
            linkedin_url = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    _executor, linkedin_finder.find_linkedin_url, extract
                ),
                timeout=15,
            )
        except asyncio.TimeoutError:
            log.warning("LinkedIn search timed out")
            linkedin_url = None
        if linkedin_url:
            current_linkedin = chosen_record.linkedin_url if chosen_record else None
            from crm_ingest.models import FieldUpdate
            proposal.field_updates.append(FieldUpdate(
                field="LinkedIn URL",
                current_value=current_linkedin,
                proposed_value=linkedin_url,
                action="add_if_empty",
                reasoning="Found via Google search.",
            ))
            await msg.reply_text(f"✅ Found LinkedIn: {linkedin_url}")
        else:
            await msg.reply_text("LinkedIn profile not found.")

    except Exception as exc:
        log.exception("Proposal generation error")
        await msg.reply_text(f"Error generating proposal: {exc}")
        return ConversationHandler.END

    session["proposal"] = proposal
    session["interaction"] = interaction
    session["person_id"] = person_id
    session["person_name"] = person_name
    session["confidence"] = confidence

    return await _show_approval(msg, session)


async def _show_approval(msg, session: dict) -> int:
    """Send the current proposal + interaction and ask for approval."""
    proposal = session["proposal"]
    interaction = session["interaction"]
    action = session["action"]
    person_name = session["person_name"]

    action_label = "Update existing record" if action == "update_existing" else "Create new record"
    proposal_text = (
        f"*Proposed action:* {action_label}\n"
        f"*Person:* {person_name}\n"
        f"*Changes:* {len(proposal.field_updates)} field update(s) + 1 interaction\n\n"
        f"{_format_proposal(proposal)}"
    )
    await msg.reply_text(_truncate(proposal_text), parse_mode=ParseMode.MARKDOWN)

    interaction_text = f"*Interaction log entry:*\n\n{_format_interaction(interaction)}"
    await msg.reply_text(_truncate(interaction_text), parse_mode=ParseMode.MARKDOWN)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve — write to Sheets", callback_data="approve"),
            InlineKeyboardButton("✏️ Edit", callback_data="edit"),
            InlineKeyboardButton("❌ Reject", callback_data="reject"),
        ]
    ])
    await msg.reply_text(
        "Write these changes to Google Sheets?",
        reply_markup=keyboard,
    )
    return WAITING_APPROVAL


def _apply_corrections(
    proposal: UpdateProposal,
    interaction: InteractionEntry,
    correction: str,
) -> tuple[UpdateProposal, InteractionEntry]:
    """Use Claude to apply the user's free-text correction to the proposal and interaction."""
    from anthropic import Anthropic
    import json

    client = Anthropic(api_key=config.anthropic_api_key)

    current = json.dumps({
        "proposal": proposal.model_dump(),
        "interaction": interaction.model_dump(),
    }, indent=2)

    tools = [
        {
            "name": "apply_corrections",
            "description": "Return the corrected proposal and interaction after applying the user's edits.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "proposal": UpdateProposal.model_json_schema(),
                    "interaction": InteractionEntry.model_json_schema(),
                },
                "required": ["proposal", "interaction"],
            },
        }
    ]

    response = client.messages.create(
        model=config.claude_model,
        max_tokens=4096,
        system=(
            "You are a CRM data editor. Apply the user's correction to the proposal and interaction. "
            "Only change what the user specifies; leave everything else exactly as-is."
        ),
        tools=tools,
        tool_choice={"type": "tool", "name": "apply_corrections"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Current proposal and interaction:\n{current}\n\n"
                    f"User's correction: {correction}\n\n"
                    "Apply the correction and return the updated data."
                ),
            }
        ],
    )

    tool_block = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_block is None:
        raise ValueError("Claude did not return a tool_use block for corrections.")

    updated_proposal = UpdateProposal.model_validate(tool_block.input["proposal"])
    updated_interaction = InteractionEntry.model_validate(tool_block.input["interaction"])
    return updated_proposal, updated_interaction


# ── Step 3: Approval ───────────────────────────────────────────────────────────

async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    session = sessions.get(chat_id)

    if not session:
        await query.edit_message_text("Session expired. Please send a new voice message.")
        return ConversationHandler.END

    if query.data == "reject":
        await query.edit_message_text("Rejected. Nothing written to Sheets.")
        await asyncio.get_running_loop().run_in_executor(
            _executor,
            _pipeline_module._log_to_review_queue,
            session["action"],
            session["person_id"],
            session["person_name"],
            session["confidence"],
            session["proposal"],
            session["interaction"],
            "rejected",
        )
        _cleanup_session(chat_id)
        return ConversationHandler.END

    # Approved — write to Sheets
    await query.edit_message_text("Writing to Google Sheets...")

    try:
        action = session["action"]
        proposal = session["proposal"]
        interaction = session["interaction"]
        person_id = session["person_id"]
        person_name = session["person_name"]
        confidence = session["confidence"]
        extract = session["extract"]

        before_record = None
        if action == "create_new":
            new_record = _pipeline_module._build_new_record(extract)
            new_record = await asyncio.get_running_loop().run_in_executor(
                _executor, people_sheet.create, new_record
            )
            person_id = new_record.person_id
            person_name = new_record.full_name
        else:
            before_record = await asyncio.get_running_loop().run_in_executor(
                _executor, people_sheet.get_by_id, person_id
            )
            await asyncio.get_running_loop().run_in_executor(
                _executor, people_sheet.apply_updates, person_id, proposal
            )

        interaction_id = await asyncio.get_running_loop().run_in_executor(
            _executor,
            interactions_sheet.append_interaction,
            person_id,
            person_name,
            interaction,
            session["raw_transcript"],
            session["cleaned"],
            str(session["audio_path"]),
        )

        # Always set Last Interaction Date; default Date First Met if empty
        interaction_date = interaction.interaction_date
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            _executor, people_sheet.write_field, person_id, "Last Interaction Date", interaction_date
        )
        current_record = await loop.run_in_executor(_executor, people_sheet.get_by_id, person_id)
        if current_record and not current_record.date_first_met:
            await loop.run_in_executor(
                _executor, people_sheet.write_field, person_id, "Date First Met", interaction_date
            )

        await asyncio.get_running_loop().run_in_executor(
            _executor,
            _pipeline_module._log_to_review_queue,
            action,
            person_id,
            person_name,
            confidence,
            proposal,
            interaction,
            "approved",
        )

        # Save undo state
        undo_state: dict = {"action": action, "person_id": person_id, "interaction_id": interaction_id}
        if before_record:
            undo_state["before_record"] = before_record.model_dump()
        _save_undo(undo_state)

        action_label = "Created" if action == "create_new" else "Updated"
        await query.message.reply_text(
            f"Done! {action_label} record for *{person_name}* and logged interaction `{interaction_id}`.",
            parse_mode=ParseMode.MARKDOWN,
        )

        # ── Ask about contact method ───────────────────────────────────────────
        session["person_id"] = person_id
        session["person_name"] = person_name

        # Skip prompt if the proposal already included a contact method
        proposal_has_contact = any(
            u.field == "Contact Method" for u in proposal.field_updates
        )
        if proposal_has_contact:
            _cleanup_session(chat_id)
            return ConversationHandler.END

        skip_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Skip", callback_data="skip_contact")]
        ])

        fresh_record = await asyncio.get_running_loop().run_in_executor(
            _executor, people_sheet.get_by_id, person_id
        )
        existing_cm = fresh_record.contact_method if fresh_record else None

        if existing_cm:
            await query.message.reply_text(
                f"Contact already on file: {existing_cm}\n\nReply to replace.",
                reply_markup=skip_keyboard,
            )
        else:
            await query.message.reply_text(
                "Add contact method?",
                reply_markup=skip_keyboard,
            )

        return WAITING_CONTACT_METHOD

    except Exception as exc:
        log.exception("Sheets write error")
        await query.message.reply_text(f"Error writing to Sheets: {exc}")
        _cleanup_session(chat_id)
        return ConversationHandler.END


# ── Step 4: Contact method ─────────────────────────────────────────────────────

async def handle_contact_method_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User sent a contact method — write it to Sheets."""
    chat_id = update.effective_chat.id
    session = sessions.get(chat_id)
    msg = update.effective_message

    if not session:
        await msg.reply_text("Session expired.")
        return ConversationHandler.END

    contact_method = msg.text.strip()
    person_id = session["person_id"]
    person_name = session["person_name"]

    try:
        await asyncio.get_running_loop().run_in_executor(
            _executor,
            people_sheet.write_field,
            person_id,
            "Contact Method",
            contact_method,
        )
        await msg.reply_text(
            f"Contact method saved for *{person_name}*: {contact_method}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        log.exception("Contact method write error")
        await msg.reply_text(f"Error saving contact method: {exc}")

    _cleanup_session(chat_id)
    return ConversationHandler.END


async def handle_skip_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped Skip — leave contact method as-is."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Contact method unchanged.")
    _cleanup_session(update.effective_chat.id)
    return ConversationHandler.END


def _cleanup_session(chat_id: int) -> None:
    sessions.pop(chat_id, {})


# ── Step 3b: Edit request ──────────────────────────────────────────────────────

async def handle_edit_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User clicked ✏️ Edit — ask them what to change."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    if not sessions.get(chat_id):
        await query.edit_message_text("Session expired. Please send a new voice message.")
        return ConversationHandler.END

    await query.edit_message_text(
        "What would you like to change? Send me a message describing the correction "
        "(e.g. \"The company should be Acme Corp\" or \"Remove the follow-up about the deck\")."
    )
    return WAITING_EDIT


async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User sent a correction — apply it via Claude and re-show the proposal."""
    chat_id = update.effective_chat.id
    session = sessions.get(chat_id)
    msg = update.effective_message

    if not session:
        await msg.reply_text("Session expired. Please send a new voice message.")
        return ConversationHandler.END

    correction = msg.text.strip()
    await msg.reply_text("Applying your correction...")

    try:
        updated_proposal, updated_interaction = await asyncio.get_running_loop().run_in_executor(
            _executor,
            _apply_corrections,
            session["proposal"],
            session["interaction"],
            correction,
        )
    except Exception as exc:
        log.exception("Correction error")
        await msg.reply_text(f"Error applying correction: {exc}")
        return await _show_approval(msg, session)

    session["proposal"] = updated_proposal
    session["interaction"] = updated_interaction

    return await _show_approval(msg, session)


# ── /find query ───────────────────────────────────────────────────────────────

def _parse_query(query: str) -> tuple[str | None, str | None, str | None]:
    """Parse query to extract field and value for direct search.
    
    Returns (field, value, search_type) where:
    - field: the column to search in
    - value: the value to search for
    - search_type: 'exact', 'contains', 'empty', or None for fallback to AI
    """
    query_lower = query.lower().strip()
    
    # Pattern: "contacts at [company]" or "people at [company]"
    if query_lower.startswith(("contacts at ", "people at ")):
        value = query[query_lower.find(" at ") + 4:].strip()
        if value in ("blank", "empty", "none", ""):
            return "company", None, "empty"
        return "company", value, "contains"
    
    # Pattern: "people in [location]"
    if query_lower.startswith("people in "):
        value = query[9:].strip()
        return "location", value, "contains"
    
    # Pattern: "tagged [tag]" or "with tag [tag]"
    if query_lower.startswith(("tagged ", "with tag ")):
        prefix = "tagged " if query_lower.startswith("tagged ") else "with tag "
        value = query[len(prefix):].strip()
        return "tags", value, "contains"
    
    # Pattern: "[name]" - assume searching by name (simple queries that look like names)
    words = query.split()
    if (len(words) <= 3 and 
        not any(word in query_lower for word in ["at", "in", "with", "tagged", "and", "or", "the", "from"]) and
        any(word[0].isupper() for word in words if word)):  # At least one word starts with capital
        return "full_name", query, "contains"
    
    # Fallback to AI search
    return None, None, None


def _query_contacts(query: str, records: list) -> str:
    # Try to parse for direct search first
    field, value, search_type = _parse_query(query)
    
    if field and search_type:
        matches = []
        for r in records:
            field_value = getattr(r, field, None)
            if field_value is None:
                field_value = ""
            
            if search_type == "empty":
                if not field_value.strip():
                    matches.append(r)
            elif search_type == "exact":
                if field_value.lower() == value.lower():
                    matches.append(r)
            elif search_type == "contains":
                if value.lower() in field_value.lower():
                    matches.append(r)
        
        if matches:
            result = f"Found {len(matches)} matching contact{'s' if len(matches) != 1 else ''}:\n\n"
            for r in matches[:10]:  # Limit to 10 results
                result += f"**{r.full_name}**\n"
                if r.company:
                    result += f"Company: {r.company}\n"
                if r.role:
                    result += f"Role: {r.role}\n"
                if r.location:
                    result += f"Location: {r.location}\n"
                if r.tags:
                    result += f"Tags: {r.tags}\n"
                result += "\n"
            if len(matches) > 10:
                result += f"... and {len(matches) - 10} more"
            return result
        else:
            return f"No contacts found matching '{query}'"
    
    # Fallback to AI-powered search
    import anthropic, json
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    people_data = [
        {
            "name": r.full_name,
            "company": r.company,
            "role": r.role,
            "location": r.location,
            "contact_method": r.contact_method,
            "where_met": r.where_met,
            "tags": r.tags,
            "relationship_strength": r.relationship_strength,
            "follow_up_tasks": r.follow_up_tasks,
            "interests": r.interests,
            "collaboration_ideas": r.collaboration_ideas,
            "important_notes": r.important_notes,
        }
        for r in records
    ]
    response = client.messages.create(
        model=config.claude_model,
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": (
                f"You are a CRM assistant. Search the contacts below and answer this query:\n\n"
                f"Query: {query}\n\n"
                f"Contacts:\n{json.dumps(people_data, indent=2)}\n\n"
                "Return a concise, formatted response with matching contacts and relevant fields. "
                "Use markdown. If no contacts match, say so clearly."
            ),
        }],
    )
    return response.content[0].text


async def handle_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_followup_reminders(context, override_chat_id=update.effective_chat.id)


async def handle_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    query_text = " ".join(context.args) if context.args else ""
    if not query_text:
        await msg.reply_text("Usage: /find <query>\nExample: /find people in San Francisco")
        return
    await msg.reply_text(f"Searching: _{query_text}_...", parse_mode=ParseMode.MARKDOWN)
    try:
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(_executor, people_sheet.load_all)
        result = await loop.run_in_executor(_executor, _query_contacts, query_text, records)
        await msg.reply_text(result, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        log.exception("Query error")
        await msg.reply_text(f"Error: {exc}")


# ── Daily follow-up reminders ─────────────────────────────────────────────────

def _check_birthday(birthday_str: str, today) -> str | None:
    """Return a reminder string if birthday is today or in 7 days, else None."""
    import re
    from datetime import date, timedelta

    if not birthday_str:
        return None

    # Parse "Month DD" or "Month DD, YYYY"
    m = re.match(
        r"(\w+)\s+(\d{1,2})(?:,\s*\d{4})?", birthday_str.strip()
    )
    if not m:
        return None

    try:
        month_name, day = m.group(1), int(m.group(2))
        bday_this_year = date(today.year, list(__import__('calendar').month_abbr).index(month_name[:3]), day)
    except Exception:
        try:
            # Try full month name
            import datetime
            bday_this_year = datetime.datetime.strptime(f"{m.group(1)} {day} {today.year}", "%B %d %Y").date()
        except Exception:
            return None

    # Also check next year in case it just passed
    delta = (bday_this_year - today).days
    if delta < 0:
        bday_this_year = bday_this_year.replace(year=today.year + 1)
        delta = (bday_this_year - today).days

    if delta == 0:
        return "🎂 *Today!*"
    elif delta <= 7:
        return f"🎂 *{delta} day{'s' if delta != 1 else ''} away* ({bday_this_year.strftime('%B %-d')})"
    return None


async def send_followup_reminders(context: ContextTypes.DEFAULT_TYPE, override_chat_id: int | None = None) -> None:
    """Check the People sheet and remind about outstanding follow-up tasks and upcoming birthdays."""
    from datetime import date
    chat_id = override_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        log.warning("TELEGRAM_CHAT_ID not set — skipping follow-up reminder.")
        return

    try:
        records = await asyncio.get_running_loop().run_in_executor(
            _executor, people_sheet.load_all
        )
        today = date.today()
        messages = []

        # ── Follow-up reminders ────────────────────────────────────────────────
        pending = [r for r in records if r.follow_up_tasks and r.follow_up_tasks.strip()]
        if pending:
            lines = ["*Follow-up reminders:*\n"]
            for r in pending:
                name = r.full_name
                company = f" ({r.company})" if r.company else ""
                contact = f" — {r.contact_method}" if r.contact_method else ""
                lines.append(f"• *{name}*{company}{contact}\n  {r.follow_up_tasks.strip()}")
            messages.append("\n\n".join(lines))

        # ── Birthday reminders ─────────────────────────────────────────────────
        bday_lines = []
        for r in records:
            reminder = _check_birthday(r.birthday or "", today)
            if reminder:
                company = f" ({r.company})" if r.company else ""
                bday_lines.append(f"{reminder} — *{r.full_name}*{company}")
        if bday_lines:
            messages.append("*Birthday reminders:*\n\n" + "\n".join(bday_lines))

        if not messages:
            return  # Nothing to report — stay quiet

        for text in messages:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception:
        log.exception("Error sending follow-up reminders")


# ── /undo ─────────────────────────────────────────────────────────────────────

def _perform_undo(state: dict) -> str:
    action = state["action"]
    person_id = state["person_id"]
    interaction_id = state["interaction_id"]

    # Always delete the interaction log entry
    interactions_sheet.delete_by_id(interaction_id)

    if action == "create_new":
        people_sheet.delete_by_id(person_id)
        return f"Undone: deleted new record and interaction `{interaction_id}`."
    else:
        before = PersonRecord.model_validate(state["before_record"])
        people_sheet.restore_record(before)
        return f"Undone: restored *{before.full_name}* to previous state and deleted interaction `{interaction_id}`."


async def handle_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    state = _load_undo()
    if not state:
        await msg.reply_text("Nothing to undo.")
        return
    await msg.reply_text("Undoing last action...")
    try:
        result = await asyncio.get_running_loop().run_in_executor(_executor, _perform_undo, state)
        _clear_undo()
        await msg.reply_text(result, parse_mode=ParseMode.MARKDOWN)
    except Exception as exc:
        log.exception("Undo error")
        await msg.reply_text(f"Undo failed: {exc}")


# ── Fallback / cancel ─────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    _cleanup_session(chat_id)
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise EnvironmentError(
            "TELEGRAM_BOT_TOKEN not set. Add it to your .env file or export it."
        )

    app = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_transcript),
        ],
        states={
            WAITING_IDENTITY: [
                CallbackQueryHandler(handle_identity_choice, pattern=r"^identity:"),
            ],
            WAITING_APPROVAL: [
                CallbackQueryHandler(handle_approval, pattern=r"^(approve|reject)$"),
                CallbackQueryHandler(handle_edit_request, pattern=r"^edit$"),
            ],
            WAITING_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_text),
            ],
            WAITING_CONTACT_METHOD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_contact_method_text),
                CallbackQueryHandler(handle_skip_contact, pattern=r"^skip_contact$"),
            ],
        },
        fallbacks=[
            MessageHandler(filters.TEXT & filters.Regex(r"(?i)^/cancel"), cancel),
        ],
        per_chat=True,
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("find", handle_find))
    app.add_handler(CommandHandler("undo", handle_undo))
    app.add_handler(CommandHandler("reminders", handle_reminders))

    # Daily follow-up reminder at 9:00 AM
    import datetime
    app.job_queue.run_daily(
        send_followup_reminders,
        time=datetime.time(hour=9, minute=0),
        name="daily_followup_reminder",
    )

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
