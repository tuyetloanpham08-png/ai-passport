"""Wire protocol between the device and the relay (version 1).

Device -> relay
    text   {"t":"start","rate":16000}   key down: begin one utterance
    binary PCM signed 16-bit little-endian, mono, 16 kHz
    text   {"t":"end"}                  key up: process the utterance
    text   {"t":"cancel"}               drop the utterance / stop the reply

Relay -> device
    text   {"t":"ready"}                recording accepted, speak now
    text   {"t":"asr","text":...}       what the relay heard
    text   {"t":"reply","text":...}     the answer, already display-safe
    text   {"t":"audio","rate":16000}   binary PCM frames follow
    binary PCM signed 16-bit little-endian, mono, 16 kHz
    text   {"t":"error","code":...,"msg":...}
    text   {"t":"done"}                 always the last frame of a turn
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
MAX_UTTERANCE_SECONDS = 15
MIN_UTTERANCE_SECONDS = 0.3
MAX_UTTERANCE_BYTES = MAX_UTTERANCE_SECONDS * SAMPLE_RATE * BYTES_PER_SAMPLE
MIN_UTTERANCE_BYTES = int(MIN_UTTERANCE_SECONDS * SAMPLE_RATE * BYTES_PER_SAMPLE)
# The device parses JSON into a fixed buffer; keep every text frame well below it.
MAX_TEXT_FRAME_BYTES = 1024
AUDIO_CHUNK_BYTES = 2048


def encode(message: dict) -> str:
    """Serialise a relay -> device message, refusing frames the device cannot parse."""
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_TEXT_FRAME_BYTES:
        raise ValueError("text frame exceeds MAX_TEXT_FRAME_BYTES")
    return text


@dataclass
class Utterance:
    """PCM collected between key down and key up, with a hard size cap."""

    data: bytearray = field(default_factory=bytearray)
    overflowed: bool = False

    def append(self, chunk: bytes) -> bool:
        if self.overflowed:
            return False
        if len(self.data) + len(chunk) > MAX_UTTERANCE_BYTES:
            self.overflowed = True
            return False
        self.data.extend(chunk)
        return True

    @property
    def too_short(self) -> bool:
        return len(self.data) < MIN_UTTERANCE_BYTES
