"""
core/audio.py - core/tts.py re-export wrapper
Allows importing TTS functions via either `core.audio` or `core.tts`.
"""
from core.tts import (
    VOICE_MAPPING,
    DEFAULT_FEMALE_VOICE,
    DEFAULT_MALE_VOICE,
    DEFAULT_RATE,
    DEFAULT_PITCH,
    resolve_edge_voice,
    clean_tts_text,
    generate_speech
)

__all__ = [
    "VOICE_MAPPING",
    "DEFAULT_FEMALE_VOICE",
    "DEFAULT_MALE_VOICE",
    "DEFAULT_RATE",
    "DEFAULT_PITCH",
    "resolve_edge_voice",
    "clean_tts_text",
    "generate_speech"
]
