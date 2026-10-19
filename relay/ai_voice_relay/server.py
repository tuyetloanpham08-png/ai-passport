"""WebSocket relay server: one connection per device, one turn at a time."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .pipeline import MAX_HISTORY_MESSAGES, Pipeline
from .protocol import AUDIO_CHUNK_BYTES, SAMPLE_RATE, Utterance, encode

log = logging.getLogger("ai_voice_relay")
# Pace audio slightly faster than real time so the device ring buffer
# (about half a second) never overflows.
PACING_SECONDS = AUDIO_CHUNK_BYTES / (SAMPLE_RATE * 2) / 1.6


class Session:
    def __init__(self, ws: ServerConnection, pipeline: Pipeline) -> None:
        self._ws = ws
        self._pipeline = pipeline
        self._history: list[dict] = []
        self._utterance: Utterance | None = None
        self._turn: asyncio.Task | None = None

    async def run(self) -> None:
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    await self._on_audio(message)
                else:
                    await self._on_control(message)
        except ConnectionClosed:
            pass
        finally:
            self._cancel_turn()

    def _cancel_turn(self) -> None:
        if self._turn and not self._turn.done():
            self._turn.cancel()
        self._turn = None

    async def _send(self, message: dict) -> None:
        await self._ws.send(encode(message))

    async def _send_quiet(self, message: dict) -> None:
        try:
            await self._send(message)
        except ConnectionClosed:
            pass

    async def _on_control(self, raw: str) -> None:
        try:
            message = json.loads(raw)
            kind = message["t"]
        except (ValueError, KeyError, TypeError):
            await self._send({"t": "error", "code": "bad_frame", "msg": "invalid control frame"})
            return
        if kind == "start":
            self._cancel_turn()
            self._utterance = Utterance()
            await self._send({"t": "ready"})
        elif kind == "cancel":
            self._cancel_turn()
            self._utterance = None
        elif kind == "end":
            utterance, self._utterance = self._utterance, None
            if utterance is None:
                await self._send({"t": "error", "code": "no_utterance", "msg": "end without start"})
                await self._send({"t": "done"})
            elif utterance.overflowed:
                await self._send({"t": "error", "code": "too_long", "msg": "utterance too long"})
                await self._send({"t": "done"})
            elif utterance.too_short:
                await self._send({"t": "error", "code": "too_short", "msg": "utterance too short"})
                await self._send({"t": "done"})
            else:
                self._turn = asyncio.create_task(self._handle_turn(bytes(utterance.data)))

    async def _on_audio(self, chunk: bytes) -> None:
        if self._utterance is not None:
            self._utterance.append(chunk)

    async def _handle_turn(self, pcm: bytes) -> None:
        try:
            heard = await self._pipeline.transcribe(pcm)
            if not heard:
                await self._send({"t": "error", "code": "no_speech", "msg": "nothing heard"})
                return
            await self._send({"t": "asr", "text": heard[:200]})
            answer = await self._pipeline.reply(self._history, heard)
            self._history += [
                {"role": "user", "content": heard},
                {"role": "assistant", "content": answer},
            ]
            del self._history[:-MAX_HISTORY_MESSAGES]
            await self._send({"t": "reply", "text": answer})
            await self._speak(answer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # report to the device, keep the connection alive
            log.exception("turn failed")
            await self._send_quiet({"t": "error", "code": "turn_failed", "msg": type(exc).__name__})
        finally:
            await self._send_quiet({"t": "done"})

    async def _speak(self, text: str) -> None:
        try:
            await self._send({"t": "audio", "rate": SAMPLE_RATE})
            async for pcm in self._pipeline.synthesize(text):
                for offset in range(0, len(pcm), AUDIO_CHUNK_BYTES):
                    await self._ws.send(pcm[offset : offset + AUDIO_CHUNK_BYTES])
                    await asyncio.sleep(PACING_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("speech failed")
            await self._send({"t": "error", "code": "tts_failed", "msg": type(exc).__name__})


def token_ok(connection: ServerConnection, token: str) -> bool:
    query = parse_qs(urlparse(connection.request.path).query)
    supplied = query.get("token", [""])[0]
    return hmac.compare_digest(supplied.encode(), token.encode())


async def serve_forever(host: str, port: int, token: str, pipeline: Pipeline) -> None:
    async def handler(ws: ServerConnection) -> None:
        if not token_ok(ws, token):
            await ws.close(code=4401, reason="bad token")
            return
        log.info("device connected: %s", ws.remote_address)
        await Session(ws, pipeline).run()
        log.info("device disconnected")

    async with serve(handler, host, port, max_size=64 * 1024):
        log.info("relay listening on ws://%s:%d/v1/chat", host, port)
        await asyncio.Future()
