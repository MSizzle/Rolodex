"""
Module 1: Transcription

Converts an audio file to raw text. Supports two providers:
  - openai  → OpenAI Whisper API  (requires OPENAI_API_KEY)
  - local   → local openai-whisper package  (pip install openai-whisper)

The provider is selected via the TRANSCRIPTION_PROVIDER env var.
"""

from __future__ import annotations

import logging
from pathlib import Path

from crm_ingest.config import config

log = logging.getLogger(__name__)

SUPPORTED_AUDIO = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".flac"}


def transcribe(audio_path: Path) -> str:
    """Return raw transcript text for the given audio file."""
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    suffix = audio_path.suffix.lower()
    if suffix not in SUPPORTED_AUDIO:
        raise ValueError(
            f"Unsupported audio format '{suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_AUDIO))}"
        )

    provider = config.transcription_provider.lower()
    log.info("Transcribing %s via provider '%s'", audio_path.name, provider)

    if provider == "openai":
        return _transcribe_openai(audio_path)
    elif provider == "local":
        return _transcribe_local(audio_path)
    else:
        raise ValueError(
            f"Unknown TRANSCRIPTION_PROVIDER '{provider}'. Use 'openai' or 'local'."
        )


def _transcribe_openai(audio_path: Path) -> str:
    """Use OpenAI Whisper API."""
    if not config.openai_api_key:
        raise EnvironmentError(
            "OPENAI_API_KEY is required when TRANSCRIPTION_PROVIDER=openai"
        )

    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("Run: pip install openai")

    client = OpenAI(api_key=config.openai_api_key)

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
        )

    # response is a str when response_format="text"
    transcript = str(response).strip()
    log.info("OpenAI Whisper transcription complete (%d chars)", len(transcript))
    return transcript


def _transcribe_local(audio_path: Path) -> str:
    """Use locally installed openai-whisper package."""
    try:
        import whisper  # type: ignore
    except ImportError:
        raise ImportError(
            "Run: pip install openai-whisper\n"
            "Note: this downloads a large model on first use."
        )

    model = whisper.load_model("large-v3")
    result = model.transcribe(str(audio_path))
    transcript = result["text"].strip()
    log.info("Local Whisper transcription complete (%d chars)", len(transcript))
    return transcript
