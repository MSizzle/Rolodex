# TODO / Known Issues

## Bot

### PTBUserWarning: `per_message=False` with `CallbackQueryHandler`

**Status:** known, not yet fixed
**Severity:** low — latent UX bug, no crashes
**Source:** `crm_ingest/telegram_bot.py` — `ConversationHandler` definition in `main()`

The `ConversationHandler` mixes `MessageHandler` and `CallbackQueryHandler` states
(`WAITING_IDENTITY`, `WAITING_APPROVAL`, `WAITING_CONTACT_METHOD`), so
python-telegram-bot can't reliably track which button click belongs to which
conversation. PTB logs this warning on every startup.

**Symptom:** if a user scrolls up in Telegram and clicks an approve/reject/identity
button from an older conversation (not the most recent one), the click may be
silently ignored or routed to the wrong session.

**Why it's not fixed yet:** the clean fix requires a state-machine refactor — either
separating callback flows into their own handler group, or restructuring so each
state uses only one handler type. Neither is quick.

**Workaround until fixed:** always respond to the most recent prompt; if you click
an old button and nothing happens, send `/cancel` and start over.

**Docs:** https://github.com/python-telegram-bot/python-telegram-bot/wiki/Frequently-Asked-Questions#what-do-the-per_-settings-in-conversationhandler-do
