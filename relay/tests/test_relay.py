import asyncio
import json
import unittest

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from ai_voice_relay import charset, protocol
from ai_voice_relay.server import serve_forever, token_ok  # noqa: F401
from websockets.asyncio.server import serve

TOKEN = "t" * 16
PCM_1S = b"\x01\x00" * protocol.SAMPLE_RATE


class FakePipeline:
    def __init__(self, heard="你好", answer="你好呀，有什么可以帮你？", fail_tts=False):
        self.heard, self.answer, self.fail_tts = heard, answer, fail_tts
        self.histories = []

    async def transcribe(self, pcm):
        return self.heard

    async def reply(self, history, user_text):
        self.histories.append(list(history))
        return self.answer

    async def synthesize(self, text):
        if self.fail_tts:
            raise RuntimeError("boom")
        yield b"\x02\x00" * 3000  # 6000 bytes -> 3 chunks


async def run_server(pipeline):
    from ai_voice_relay.server import Session, token_ok as ok

    async def handler(ws):
        if not ok(ws, TOKEN):
            await ws.close(code=4401)
            return
        await Session(ws, pipeline).run()

    return await serve(handler, "127.0.0.1", 0)


async def collect_turn(ws):
    texts, audio = [], 0
    while True:
        msg = await asyncio.wait_for(ws.recv(), 5)
        if isinstance(msg, bytes):
            audio += len(msg)
            continue
        frame = json.loads(msg)
        texts.append(frame)
        if frame["t"] == "done":
            return texts, audio


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipeline = FakePipeline()
        self.server = await run_server(self.pipeline)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    def url(self, token=TOKEN):
        return f"ws://127.0.0.1:{self.port}/v1/chat?token={token}"

    async def talk(self, ws, pcm=PCM_1S):
        await ws.send(json.dumps({"t": "start", "rate": 16000}))
        self.assertEqual(json.loads(await ws.recv()), {"t": "ready"})
        for i in range(0, len(pcm), 4096):
            await ws.send(pcm[i : i + 4096])
        await ws.send(json.dumps({"t": "end"}))
        return await collect_turn(ws)

    async def test_full_turn(self):
        async with connect(self.url()) as ws:
            frames, audio = await self.talk(ws)
        self.assertEqual([f["t"] for f in frames], ["asr", "reply", "audio", "done"])
        self.assertEqual(frames[0]["text"], "你好")
        self.assertEqual(audio, 6000)

    async def test_history_carries_over(self):
        async with connect(self.url()) as ws:
            await self.talk(ws)
            await self.talk(ws)
        self.assertEqual(self.pipeline.histories[0], [])
        self.assertEqual(len(self.pipeline.histories[1]), 2)

    async def test_bad_token_rejected(self):
        with self.assertRaises(ConnectionClosed):
            async with connect(self.url("wrong")) as ws:
                await ws.recv()

    async def test_too_short_and_too_long(self):
        async with connect(self.url()) as ws:
            frames, _ = await self.talk(ws, b"\x00\x00" * 100)
            self.assertEqual(frames[0]["code"], "too_short")
            long_pcm = b"\x00\x00" * (protocol.SAMPLE_RATE * (protocol.MAX_UTTERANCE_SECONDS + 1))
            frames, _ = await self.talk(ws, long_pcm)
            self.assertEqual(frames[0]["code"], "too_long")

    async def test_no_speech(self):
        self.pipeline.heard = ""
        async with connect(self.url()) as ws:
            frames, _ = await self.talk(ws)
        self.assertEqual([f["t"] for f in frames], ["error", "done"])
        self.assertEqual(frames[0]["code"], "no_speech")

    async def test_tts_failure_still_ends_turn(self):
        self.pipeline.fail_tts = True
        async with connect(self.url()) as ws:
            frames, _ = await self.talk(ws)
        self.assertEqual([f["t"] for f in frames], ["asr", "reply", "audio", "error", "done"])
        self.assertEqual(frames[3]["code"], "tts_failed")

    async def test_bad_control_frame_keeps_connection(self):
        async with connect(self.url()) as ws:
            await ws.send("not json")
            self.assertEqual(json.loads(await ws.recv())["code"], "bad_frame")
            frames, _ = await self.talk(ws)
        self.assertEqual(frames[-1]["t"], "done")


class CharsetTests(unittest.TestCase):
    def test_common_chars_allowed_rare_dropped(self):
        self.assertEqual(charset.sanitize("你好，世界！"), "你好，世界！")
        self.assertEqual(charset.sanitize("好😀的"), "好的")
        self.assertNotIn("𠮷", charset.sanitize("𠮷野家"))

    def test_markdown_stripped_and_length_capped(self):
        self.assertEqual(charset.sanitize("**重点**：`代码`"), "重点：代码")
        out = charset.sanitize("啊" * 500)
        self.assertEqual(len(out), charset.MAX_REPLY_CHARS)
        self.assertTrue(out.endswith("…"))

    def test_level1_size(self):
        han = [c for c in charset.allowed_chars() if "一" <= c <= "鿿"]
        self.assertEqual(len(han), 3755)


class ProtocolTests(unittest.TestCase):
    def test_encode_rejects_oversized_frame(self):
        with self.assertRaises(ValueError):
            protocol.encode({"t": "reply", "text": "字" * 400})

    def test_max_reply_fits_one_frame(self):
        protocol.encode({"t": "reply", "text": "字" * charset.MAX_REPLY_CHARS})

    def test_utterance_cap(self):
        u = protocol.Utterance()
        self.assertTrue(u.append(b"\x00" * protocol.MAX_UTTERANCE_BYTES))
        self.assertFalse(u.append(b"\x00"))
        self.assertTrue(u.overflowed)


if __name__ == "__main__":
    unittest.main()
