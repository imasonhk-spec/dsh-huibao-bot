"""Exercise real WAV bytes and HTTP routing, rather than faking decoded PCM."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import struct
import threading
import urllib.error
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from deskbot_server.tts import gateway


def make_wav(samples, *, bits=32, rate=24000, channels=1, encoding=3,
             extensible=False, unknown_length=False):
    dtype = {1: {16: "<i2", 32: "<i4"}, 3: {32: "<f4", 64: "<f8"}}[encoding][bits]
    audio = np.asarray(samples, dtype=dtype).tobytes()
    alignment = channels * bits // 8
    fmt = struct.pack("<HHIIHH", 0xFFFE if extensible else encoding, channels,
                      rate, rate * alignment, alignment, bits)
    if extensible:
        fmt += struct.pack("<HHII", 22, bits, 4 if channels == 1 else 3, encoding)
        fmt += bytes.fromhex("00001000800000aa00389b71")
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    # Unknown odd-sized metadata with RIFF padding must not become audio.
    chunks += b"JUNK" + struct.pack("<I", 3) + b"xyz\0"
    chunks += b"data" + struct.pack("<I", 0xFFFFFFFF if unknown_length else len(audio)) + audio
    return b"RIFF" + struct.pack("<I", 0xFFFFFFFF if unknown_length else 4 + len(chunks)) + b"WAVE" + chunks


@pytest.mark.parametrize("bits,channels,extensible", [(32, 1, False), (64, 1, False), (32, 2, False), (32, 1, True), (64, 2, True)])
def test_float_wav_decodes_and_resamples_without_ffmpeg(bits, channels, extensible):
    rate = 24000
    source = 0.5 * np.sin(2 * np.pi * 440 * np.arange(rate // 5) / rate)
    raw = make_wav(np.repeat(source, channels), bits=bits, channels=channels, extensible=extensible)
    pcm = gateway.wav_to_pcm(raw)
    decoded = np.frombuffer(pcm, dtype="<i2")
    assert abs(len(decoded) - 3200) <= 1
    assert 16000 < np.max(decoded) <= 16384
    assert -16384 <= np.min(decoded) < -16000


def test_float_extrema_clip_instead_of_wrapping():
    raw = make_wav([-2, -1, -0.5, 0, 0.5, 1, 2], rate=16000)
    assert np.frombuffer(gateway.wav_to_pcm(raw), dtype="<i2").tolist() == [
        -32768, -32768, -16384, 0, 16384, 32767, 32767]


@pytest.mark.parametrize("encoding", [1, 3])
def test_unknown_length_stream_wav(encoding):
    raw = make_wav([0, 0.25, -0.25] if encoding == 3 else [0, 1000, -1000],
                   encoding=encoding, bits=32, rate=16000, unknown_length=True)
    assert len(gateway.wav_to_pcm(raw)) == 6


def test_extensible_integer_pcm_preserves_samples():
    raw = make_wav([-1000, 0, 1000], encoding=1, bits=16, rate=16000, extensible=True)
    assert np.frombuffer(gateway.wav_to_pcm(raw), dtype="<i2").tolist() == [-1000, 0, 1000]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_float_samples_rejected(invalid):
    with pytest.raises(ValueError, match="无效采样"):
        gateway.wav_to_pcm(make_wav([0, invalid]))


def test_truncated_stream_float_sample_rejected():
    with pytest.raises(ValueError, match="对齐"):
        gateway.wav_to_pcm(make_wav([0, 0.1], unknown_length=True)[:-1])


def test_invalid_alignment_rejected():
    raw = bytearray(make_wav([0, 0.1]))
    struct.pack_into("<H", raw, 32, 1)
    with pytest.raises(ValueError, match="对齐"):
        gateway.wav_to_pcm(bytes(raw))


@pytest.fixture
def local_tts_gateway(monkeypatch):
    """Local HTTP response still passes through production safe_provider_urlopen."""
    calls = []
    source = 0.4 * np.sin(2 * np.pi * 500 * np.arange(2400) / 24000)
    raw = make_wav(source, extensible=True)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("DESKBOT_PROVIDER_PRIVATE_ORIGINS", origin)
    cfg = gateway.GatewayTtsConfig(endpoint=origin + "/aigateway/v1/audio/speech", api_key="synthetic-test-key", timeout_seconds=3)
    monkeypatch.setattr(gateway, "load_dotenv", lambda **kw: False)
    monkeypatch.setenv("TTS_PROVIDER", "openai_compatible")
    try:
        yield cfg, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_float_wav_request_to_device_pcm(local_tts_gateway):
    config, calls = local_tts_gateway
    chunks = []
    result = asyncio.run(gateway.synthesize_gateway_tts("你好小歪", config, on_pcm=chunks.append))
    assert result.sample_rate == 16000 and b"".join(chunks) == result.pcm
    assert 3198 <= len(result.pcm) <= 3200
    path, headers, payload = calls[0]
    assert path == "/aigateway/v1/audio/speech"
    assert headers["Authorization"] == "Bearer synthetic-test-key"
    assert headers["Accept"] == "audio/wav"
    assert payload == {"model": gateway.MODEL, "input": "你好小歪", "voice": "aiden", "response_format": "wav"}


@pytest.mark.parametrize("path", ["/api/gateway/tts/test", "/app/api/tts/preview", "/api/doubao_tts/synthesize"])
def test_every_preview_route_uses_configured_gateway_and_returns_playable_wav(monkeypatch, local_tts_gateway, path):
    from flask import Flask
    from deskbot_server.web.blueprints import app_bp, debug_bp, gateway_bp
    cfg, calls = local_tts_gateway
    monkeypatch.setattr(gateway_bp, "load_gateway_tts_config", lambda: cfg)
    monkeypatch.setattr(app_bp, "_consume_settings_test_quota", lambda: ({}, None))
    app = Flask(__name__)
    for bp in (app_bp.bp, debug_bp.bp, gateway_bp.bp):
        app.register_blueprint(bp)
    response = app.test_client().post(path, json={"text": "你好小歪", "speaker": "vivian", "endpoint": "https://ignored.invalid", "api_key": "ignored"})
    assert response.status_code == 200, response.json
    assert response.json["provider"] == "openai_compatible"
    assert response.json["speaker"] == "vivian"
    assert len(calls) == 1 and calls[0][2]["voice"] == "vivian"
    with wave.open(io.BytesIO(base64.b64decode(response.json["wav_base64"]))) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        assert wav.getnframes() > 1000
    assert "synthetic-test-key" not in response.text


def test_generic_tts_settings_and_legacy_page_follow_selected_provider(monkeypatch):
    from flask import Flask
    from deskbot_server.web.blueprints import app_bp, debug_bp, gateway_bp
    cfg = gateway.GatewayTtsConfig(api_key="synthetic-secret")
    monkeypatch.setattr(gateway, "load_dotenv", lambda **kw: False)
    monkeypatch.setenv("TTS_PROVIDER", "openai_compatible")
    monkeypatch.setattr(gateway, "load_gateway_tts_config", lambda: cfg)
    app = Flask(__name__)
    for bp in (app_bp.bp, debug_bp.bp, gateway_bp.bp):
        app.register_blueprint(bp)
    client = app.test_client()
    config = client.get("/app/api/tts/config").json["config"]
    assert config["provider"] == "openai_compatible" and config["api_key_set"]
    assert [s["id"] for s in client.get("/app/api/tts/speakers").json["speakers"]] == list(gateway.VOICES)
    assert client.get("/debug/tts").location == "/voice"


@pytest.mark.parametrize("http_error", [False, True])
def test_gateway_error_detail_is_reported_without_credentials(monkeypatch, http_error):
    cfg = gateway.GatewayTtsConfig(api_key="private-test-token")
    body = json.dumps({"error": {"message": "unsupported voice; token private-test-token Bearer another-token"}}).encode()
    def fake_open(*args, **kwargs):
        if http_error:
            raise urllib.error.HTTPError(cfg.endpoint, 422, "invalid", {}, io.BytesIO(body))
        return io.BytesIO(body)
    monkeypatch.setattr(gateway, "safe_provider_urlopen", fake_open)
    with pytest.raises((RuntimeError, ValueError), match="unsupported voice") as caught:
        gateway._request_tts("hello", cfg)
    assert "private-test-token" not in str(caught.value)
    assert "another-token" not in str(caught.value)
