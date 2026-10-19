<p align="right">
  <strong>简体中文</strong> · <a href="README.md">English</a>
</p>

# AI 语音对话中继服务

中继服务跑在和设备同一个 Wi-Fi 下的电脑上。设备通过 WebSocket 把麦克风音频推给它，
它把语音转成文字（faster-whisper），交给 Claude 回答，再把回答转成语音（edge-tts）
推回设备。API Key 只放在中继服务上，不会进入固件。

```text
设备 --PCM--> 中继 --> Whisper --> Claude --> edge-tts --PCM--> 设备
设备 <-- 识别文字 / 回复文字 -------------------------------------+
```

## 运行

需要 Python 3.10 以上和 `ffmpeg`（macOS 上用 `brew install ffmpeg`）。

```bash
cd relay
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填入 ANTHROPIC_API_KEY 和 RELAY_TOKEN
set -a; source .env; set +a
python -m ai_voice_relay
```

第一次说话时会下载 Whisper 模型，只慢这一次。默认模型是 `claude-opus-5-5`，
想要更低延迟或成本，可以设置 `ANTHROPIC_MODEL` 换成更小的模型。

## 测试

```bash
pip install websockets anthropic
python -m unittest discover -s tests -v
```

测试用的是假的处理流程：覆盖协议、限制、出错路径和可显示字符过滤，
但不会真正调用 Claude、Whisper 或 edge-tts。

## 协议（版本 1）

地址：`ws://<主机>:8765/v1/chat?token=<RELAY_TOKEN>`。口令错误会以 4401 关闭连接。
链路没有加密，只能在可信的局域网里使用。

| 方向 | 帧 | 含义 |
| --- | --- | --- |
| 设备到中继 | 文本 `{"t":"start","rate":16000}` | 按下按键，开始一句话 |
| 设备到中继 | 二进制 | PCM s16le，单声道，16 kHz，每帧最多 4096 字节 |
| 设备到中继 | 文本 `{"t":"end"}` | 松开按键，开始处理 |
| 设备到中继 | 文本 `{"t":"cancel"}` | 丢弃这句话并停止回复 |
| 中继到设备 | 文本 `{"t":"ready"}` | 已开始接收录音 |
| 中继到设备 | 文本 `{"t":"asr","text":...}` | 识别出的文字 |
| 中继到设备 | 文本 `{"t":"reply","text":...}` | 回答，已过滤成可显示字符 |
| 中继到设备 | 文本 `{"t":"audio","rate":16000}` | 之后是二进制 PCM 帧（每帧 2048 字节） |
| 中继到设备 | 文本 `{"t":"error","code":...,"msg":...}` | `too_short`、`too_long`、`no_speech`、`turn_failed`、`tts_failed`、`bad_frame`、`no_utterance` |
| 中继到设备 | 文本 `{"t":"done"}` | 每一轮的最后一帧 |

限制：一句话 0.3 到 15 秒；每个文本帧小于 1024 字节；回复最多 200 个字。
中继按约 1.6 倍实时速度发送音频，避免设备端环形缓冲溢出。

## 可显示字符

回复只保留 ASCII、GB2312 一级汉字（最常用的 3755 个简体字）和基本中文标点。
固件的字库子集至少要覆盖这个范围，否则缺的字会显示成空白。
