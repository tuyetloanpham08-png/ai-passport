"""Speech-to-text, Claude, and text-to-speech behind one small interface."""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator, Protocol

from .charset import sanitize
from .protocol import SAMPLE_RATE

SYSTEM_PROMPT = (
    "你是放在口袋里的语音助手。用户通过一个带小屏幕的随身设备和你对话，"
    "你的回答会显示在 240×320 的小屏幕上，同时被朗读出来。\n"
    "要求：用简体中文、口语化、直接回答，不超过 120 个字；"
    "不要使用 Markdown、列表、表情符号，也不要夹杂英文长句；"
    "只使用常用汉字和普通标点；不确定的事就直接说不知道。"
)
REFUSAL_TEXT = "这个问题我没法回答。"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_HISTORY_MESSAGES = 12


class Pipeline(Protocol):
    async def transcribe(self, pcm: bytes) -> str: ...

    async def reply(self, history: list[dict], user_text: str) -> str: ...

    def synthesize(self, text: str) -> AsyncIterator[bytes]: ...


class ClaudePipeline:
    """Whisper (faster-whisper) -> Claude -> edge-tts, all configured by env."""

    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic()
        # claude-opus-5-5 is the API default; override for lower latency or cost.
        self._model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5")
        self._whisper_size = os.environ.get("WHISPER_MODEL", "small")
        self._voice = os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
        self._whisper = None

    async def transcribe(self, pcm: bytes) -> str:
        return await asyncio.to_thread(self._transcribe_blocking, pcm)

    def _transcribe_blocking(self, pcm: bytes) -> str:
        import numpy as np

        if self._whisper is None:
            from faster_whisper import WhisperModel

            self._whisper = WhisperModel(self._whisper_size, device="cpu", compute_type="int8")
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._whisper.transcribe(
            audio,
            language="zh",
            beam_size=1,
            vad_filter=True,
            initial_prompt="以下是普通话的句子。",
        )
        return "".join(segment.text for segment in segments).strip()

    async def reply(self, history: list[dict], user_text: str) -> str:
        messages = history[-MAX_HISTORY_MESSAGES:] + [{"role": "user", "content": user_text}]
        response = await self._client.beta.messages.create(
            model=self._model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=messages,
            # Voice turns want a quick answer; effort is the only thinking control
            # on claude-opus-5-5.
            output_config={"effort": "low"},
            betas=[FALLBACK_BETA],
            extra_body={"fallbacks": "default"},
        )
        if response.stop_reason == "refusal":
            return REFUSAL_TEXT
        text = "".join(block.text for block in response.content if block.type == "text")
        return sanitize(text)

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        import edge_tts

        mp3 = bytearray()
        async for chunk in edge_tts.Communicate(text, self._voice).stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", "pipe:0",
            "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pcm, err = await process.communicate(bytes(mp3))
        if process.returncode != 0 or not pcm:
            raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[:200]}")
        yield pcm
