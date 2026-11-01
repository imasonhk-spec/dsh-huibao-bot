"""Speech protocol integration against a real, local HTTP gateway.

No transport mocks and no live credentials: these tests exercise the actual
safe_provider_urlopen, multipart upload, SSE reads, and WAV decoding. A terminal
SSE event is followed by an intentionally unfinished HTTP chunked stream, so a
client that waits for EOF fails promptly instead of appearing to work.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import threading
import wave
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from deskbot_server import env
from deskbot_server.core.ports.asr import (
    AsrConfigurationError,
    AsrProviderResponseError,
)
from deskbot_server.core.settings import AsrSettings
from deskbot_server.infrastructure.asr.openai_compat import OpenAiCompatibleAsrAdapter
from deskbot_server.tts import gateway

FAKE_KEY = "integration-fake-key-never-a-real-credential"
ASR_MODEL = "FunAudioLLM/Fun-ASR-Nano-2512"
PCM_INPUT = struct.pack("<h", 1000) * 1600


@dataclass
class Reply:
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    status: int = 200
    # A tuple, including (), selects HTTP chunked transfer encoding.
    chunks: tuple[bytes, ...] | None = None
    keep_open: bool = False


class LocalGateway:
    def __init__(self):
        self.reply = Reply()
        self.requests = []
        self.release = threading.Event()
        self.stream_eof_sent = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                owner.requests.append({
                    "path": self.path,
                    "headers": self.headers,
                    "body": self.rfile.read(length),
                })
                reply = owner.reply
                self.send_response(reply.status)
                self.send_header("Content-Type", reply.content_type)
                if reply.chunks is None:
                    self.send_header("Content-Length", str(len(reply.body)))
                else:
                    self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    if reply.chunks is None:
                        self.wfile.write(reply.body)
                        self.wfile.flush()
                    else:
                        for chunk in reply.chunks:
                            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                            self.wfile.flush()
                            # Split payload/UTF-8 across real socket reads.
                            owner.release.wait(0.01)
                        if reply.keep_open:
                            owner.release.wait(2.5)
                        owner.stream_eof_sent.set()
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Returning at the terminal event closes the client.
                finally:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        self.thread.start()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


@pytest.fixture
def local_gateway(monkeypatch, tmp_path):
    # Use the real dotenv loader against an empty test file. Never read or
    # replace the private .env shipped with the user package.
    test_env = tmp_path / ".env"
    test_env.write_text("", encoding="utf-8")
    monkeypatch.setattr(env, "ENV_FILE", test_env)
    monkeypatch.setattr(env, "_last_signature", None)
    monkeypatch.setattr(env, "_file_managed_values", {})
    with patch.dict(os.environ, {}, clear=True):
        server = LocalGateway()
        os.environ.update({
            "DESKBOT_PROVIDER_PRIVATE_ORIGINS": server.origin,
            "ASR_PROVIDER": "openai_compatible",
            "ASR_ENDPOINT": server.origin + "/aigateway/v1/audio/transcriptions",
            "ASR_MODEL": ASR_MODEL,
            "ASR_API_KEY": FAKE_KEY,
            "ASR_LANGUAGE": "auto",
            "ASR_RESPONSE_FORMAT": "text",
            "ASR_STREAM": "true",
            "ASR_TIMEOUT_SECONDS": "1",
            "TTS_PROVIDER": "openai_compatible",
            "TTS_ENDPOINT": server.origin + "/aigateway/v1/audio/speech",
            "TTS_MODEL": gateway.MODEL,
            "TTS_API_KEY": FAKE_KEY,
            "TTS_VOICE": "aiden",
            "TTS_TIMEOUT_SECONDS": "1",
        })
        try:
            yield server
        finally:
            server.close()


def transcribe():
    # Resolve config through the public adapter, including real .env loading.
    adapter = OpenAiCompatibleAsrAdapter(AsrSettings())
    return asyncio.run(adapter.transcribe(PCM_INPUT, 16000))


def multipart_parts(request):
    message = BytesParser(policy=policy.default).parsebytes(
        ("Content-Type: " + request["headers"]["Content-Type"] + "\r\n\r\n").encode()
        + request["body"]
    )
    assert message.is_multipart()
    parts = list(message.iter_parts())
    names = [part.get_param("name", header="Content-Disposition") for part in parts]
    assert len(names) == len(set(names)), "multipart fields must not be duplicated"
    return dict(zip(names, parts))


@pytest.mark.parametrize("terminal", [
    'data: {"type":"transcript.text.done","text":"你好小歪"}\n\n',
    'event: transcript.text.done\ndata: {"text":"你好小歪"}\n\n',
    'data: [DONE]\n\n',
])
def test_asr_real_multipart_and_sse_finishes_before_http_eof(local_gateway, terminal):
    events = (
        'data: {"type":"transcript.text.delta","delta":"你好"}\n\n'
        'data: {"type":"transcript.text.delta","delta":"小歪"}\n\n'
        + terminal
    ).encode()
    # Deliberately split in the middle of a multi-byte Chinese character.
    split = events.index("你".encode()) + 1
    local_gateway.reply = Reply(
        content_type="text/event-stream", chunks=(events[:split], events[split:]), keep_open=True,
    )
    assert transcribe() == "你好小歪"
    assert not local_gateway.stream_eof_sent.is_set(), "ASR incorrectly waited for HTTP EOF"
    assert len(local_gateway.requests) == 1
    request = local_gateway.requests[0]
    assert request["path"] == "/aigateway/v1/audio/transcriptions"
    assert request["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert "text/event-stream" in request["headers"]["Accept"]
    parts = multipart_parts(request)
    assert set(parts) == {"model", "response_format", "stream", "file"}
    assert parts["model"].get_payload(decode=True).decode() == ASR_MODEL
    assert parts["response_format"].get_payload(decode=True) == b"text"
    assert parts["stream"].get_payload(decode=True) == b"true"
    assert parts["file"].get_content_type() == "audio/wav"
    assert parts["file"].get_filename().endswith(".wav")
    with wave.open(io.BytesIO(parts["file"].get_payload(decode=True)), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert wav.readframes(wav.getnframes()) == PCM_INPUT


@pytest.mark.parametrize("event", [
    'event: error\ndata: {"message":"model unavailable"}\n\n',
    'data: {"type":"error","error":{"message":"model unavailable"}}\n\n',
])
def test_asr_http_200_error_event_is_not_speech_or_a_timeout(local_gateway, event):
    local_gateway.reply = Reply(
        content_type="text/event-stream", chunks=(event.encode(),), keep_open=True,
    )
    with pytest.raises(AsrProviderResponseError):
        transcribe()
    assert not local_gateway.stream_eof_sent.is_set(), "ASR waited for EOF after an error event"


def test_asr_still_accepts_plain_text_when_gateway_ignores_stream(local_gateway):
    local_gateway.reply = Reply(body="一二三四五".encode())
    assert transcribe() == "一二三四五"


def test_private_origin_permission_is_exact_not_global(local_gateway, monkeypatch):
    monkeypatch.setenv("DESKBOT_PROVIDER_PRIVATE_ORIGINS", "http://127.0.0.1:1")
    with pytest.raises(AsrConfigurationError):
        transcribe()
    assert local_gateway.requests == []


def float_wav_24khz():
    # IEEE_FLOAT format 3, mono float32, 100 ms, with valid RIFF/fmt/fact/data.
    samples = [0.25] * 1200 + [-0.25] * 1200
    pcm = struct.pack("<" + "f" * len(samples), *samples)
    fmt = struct.pack("<HHIIHH", 3, 1, 24000, 24000 * 4, 4, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"fact" + struct.pack("<II", 4, len(samples))
    chunks += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def test_tts_real_json_request_and_float_wav_to_device_pcm(local_gateway):
    local_gateway.reply = Reply(body=float_wav_24khz(), content_type="audio/wav")
    chunks = []
    result = asyncio.run(gateway.synthesize_gateway_tts("你好，我是龙数小歪。", on_pcm=chunks.append))
    assert result.sample_rate == 16000
    assert len(result.pcm) == 3200  # 100 ms * 16 kHz * signed 16-bit mono.
    assert result.pcm == b"".join(chunks)
    samples = struct.unpack("<1600h", result.pcm)
    assert samples[100] == 8192 and samples[1200] == -8192
    assert len(local_gateway.requests) == 1
    request = local_gateway.requests[0]
    assert request["path"] == "/aigateway/v1/audio/speech"
    assert request["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert request["headers"]["Accept"] == "audio/wav"
    assert request["headers"]["Content-Type"] == "application/json"
    assert json.loads(request["body"]) == {
        "model": gateway.MODEL,
        "input": "你好，我是龙数小歪。",
        "voice": "aiden",
        "response_format": "wav",
    }


@pytest.mark.parametrize("status", [200, 400])
def test_tts_real_gateway_error_is_useful_and_redacts_key(local_gateway, status):
    local_gateway.reply = Reply(
        body=json.dumps({"error": {"message": f"voice unavailable; token {FAKE_KEY}"}}).encode(),
        content_type="application/json", status=status,
    )
    with pytest.raises((ValueError, RuntimeError)) as caught:
        asyncio.run(gateway.synthesize_gateway_tts("你好"))
    message = str(caught.value)
    assert "voice unavailable" in message
    assert FAKE_KEY not in message
    if status != 200:
        assert f"HTTP {status}" in message
