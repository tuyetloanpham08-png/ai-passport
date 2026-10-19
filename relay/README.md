<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# AI Voice Chat Relay

The relay runs on a computer on the same Wi-Fi as the device. The device streams
microphone audio to it over WebSocket; the relay turns speech into text
(faster-whisper), asks Claude, turns the answer into speech (edge-tts), and
streams it back. API keys stay on the relay and never reach the firmware.

```text
device --PCM--> relay --> Whisper --> Claude --> edge-tts --PCM--> device
device <-- asr / reply text frames ----------------------------------+
```

## Run

Requires Python 3.10+ and `ffmpeg` (`brew install ffmpeg` on macOS).

```bash
cd relay
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in ANTHROPIC_API_KEY and RELAY_TOKEN
set -a; source .env; set +a
python -m ai_voice_relay
```

The first utterance downloads the Whisper model, so it is slow once. The default
model is `claude-opus-5-5`; set `ANTHROPIC_MODEL` to trade quality for latency.

## Test

```bash
pip install websockets anthropic
python -m unittest discover -s tests -v
```

The tests use a fake pipeline: they cover the protocol, limits, error paths and
the display-safe character filter, but they do not call Claude, Whisper or
edge-tts.

## Protocol (version 1)

Endpoint: `ws://<host>:8765/v1/chat?token=<RELAY_TOKEN>`. A wrong token closes the
connection with code 4401. The link is unencrypted, so use it on a trusted LAN only.

| Direction | Frame | Meaning |
| --- | --- | --- |
| device to relay | text `{"t":"start","rate":16000}` | key down, begin one utterance |
| device to relay | binary | PCM s16le, mono, 16 kHz, at most 4096 bytes per frame |
| device to relay | text `{"t":"end"}` | key up, process the utterance |
| device to relay | text `{"t":"cancel"}` | drop the utterance and stop the reply |
| relay to device | text `{"t":"ready"}` | recording accepted |
| relay to device | text `{"t":"asr","text":...}` | what was heard |
| relay to device | text `{"t":"reply","text":...}` | the answer, display-safe |
| relay to device | text `{"t":"audio","rate":16000}` | binary PCM frames follow (2048 bytes each) |
| relay to device | text `{"t":"error","code":...,"msg":...}` | `too_short`, `too_long`, `no_speech`, `turn_failed`, `tts_failed`, `bad_frame`, `no_utterance` |
| relay to device | text `{"t":"done"}` | always the last frame of a turn |

Limits: an utterance is 0.3 to 15 seconds; every text frame stays under 1024
bytes; a reply is at most 200 characters. The relay paces audio at about 1.6 times
real time so the device ring buffer does not overflow.

## Display-safe text

The reply is filtered to ASCII plus GB2312 level 1 (the 3755 most common
simplified characters) and basic Chinese punctuation. The firmware font subset
must cover at least this set; otherwise missing glyphs render blank.
