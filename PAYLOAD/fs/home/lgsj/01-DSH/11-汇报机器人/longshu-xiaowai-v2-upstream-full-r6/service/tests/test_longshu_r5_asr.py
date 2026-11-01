"""Regression coverage for gateway responses and actual WAV recognition tests."""

import asyncio
import io
import wave

import pytest
from flask import Flask

from deskbot_server.core.ports.asr import (
    AsrAuthenticationError,
    AsrInputError,
    AsrProviderResponseError,
)
from deskbot_server.core.settings import AsrSettings
from deskbot_server.infrastructure.asr.openai_compat import (
    OpenAiCompatibleAsrAdapter,
    _read_response_body,
    parse_transcription_response,
)


@pytest.mark.parametrize("raw", [
    b'data: {"type":"error","message":"private provider detail"}\n\n',
    b'event: error\n\n',
    b'data: {"type":"transcript.failed"}\n\n',
    b'data: {"request_id":"not recognized speech"}\n\n',
    b'[{"text":"not a transcription response"}]',
])
def test_invalid_or_failed_response_does_not_become_empty_success(raw):
    with pytest.raises(AsrProviderResponseError) as caught:
        parse_transcription_response(raw, response_format="text")
    assert "private provider detail" not in str(caught.value)


def test_stream_embedded_auth_failure_is_classified_without_echoing_body():
    with pytest.raises(AsrAuthenticationError) as caught:
        parse_transcription_response(
            b'data: {"type":"error","code":401,"message":"secret-echo"}\n\n',
            response_format="text",
        )
    assert caught.value.status_code == 401
    assert "secret-echo" not in str(caught.value)


def test_sse_bare_cr_and_optional_space_are_preserved():
    raw = b'data: {"delta":"hello"}\r\rdata:  world\r\rdata: [DONE]\r\r'
    assert parse_transcription_response(raw, response_format="text") == "hello world"


def test_nullable_error_field_is_not_itself_a_failure():
    assert parse_transcription_response(b'{"text":"ok","error":null}') == "ok"


@pytest.mark.parametrize("final", [
    b'data: {"type":"transcript.text.done","text":"hello"}\n\n',
    b'data: {"text":"hello","is_final":true}\n\n',
    b'data: {"delta":"hello"}\n\ndata: [DONE]\n\n',
])
def test_finished_transcript_ignores_later_incomplete_events_in_same_socket_chunk(final):
    assert parse_transcription_response(final + b'data: {"type":', response_format="text") == "hello"


@pytest.mark.parametrize("completion", [
    b'data: [DONE]\n\n',
    b'data: {"type":"transcript.text.done","text":"hello"}\n\n',
    b'event: completed\ndata: {"text":"hello"}\n\n',
    b'data: {"text":"hello","is_final":true}\n\n',
    b'event: error\ndata: provider-failed\n\n',
])
def test_read_stops_after_complete_event_without_waiting_for_socket_eof(completion):
    class Response:
        def __init__(self):
            self.chunks = iter([b'data: {"delta":"hello"}\n\n', completion[:5], completion[5:]])

        def read1(self, _size):
            # Exhausting this iterator represents a blocking wait on a gateway
            # that keeps sending heartbeats after the result has completed.
            return next(self.chunks)

    assert _read_response_body(Response(), stream=True).endswith(completion)


def test_read_raises_error_event_without_waiting_for_socket_eof():
    class Response:
        def read1(self, _size):
            return b'data: {"type":"error","message":"secret echo"}\n\n'

    with pytest.raises(AsrProviderResponseError, match="网关返回错误"):
        _read_response_body(Response(), stream=True)


def test_odd_pcm_is_rejected_before_resolving_credentials():
    with pytest.raises(AsrInputError, match="16 位"):
        asyncio.run(OpenAiCompatibleAsrAdapter(AsrSettings()).transcribe(b"\x01", 16000))


def wav_bytes(*, channels=1, width=2, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\x01" * 100 * channels * width)
    return buf.getvalue()


@pytest.fixture()
def asr_client(monkeypatch):
    from deskbot_server.web.blueprints import asr_bp

    monkeypatch.setattr(asr_bp, "_consume_settings_test_quota", lambda: ({}, None))
    monkeypatch.setattr(asr_bp, "_runtime_settings", lambda: object())
    app = Flask(__name__)
    app.register_blueprint(asr_bp.bp)
    app.testing = True
    return app.test_client()


def set_adapter(monkeypatch, recognized_text, observed=None):
    from deskbot_server.web.blueprints import asr_bp

    class Adapter:
        async def transcribe(self, pcm, rate):
            if observed is not None:
                observed.append((pcm, rate))
            return recognized_text

    monkeypatch.setattr(asr_bp, "build_asr_adapter", lambda _settings: Adapter())


def test_wav_upload_recognition_uses_actual_audio_and_downmixes_stereo(asr_client, monkeypatch):
    observed = []
    set_adapter(monkeypatch, "你好小歪", observed)
    response = asr_client.post("/api/setup/asr/test", data={
        "audio": (io.BytesIO(wav_bytes(channels=2)), "sample.wav"),
    })
    body = response.get_json()
    assert response.status_code == 200
    assert body["test_kind"] == "speech_recognition"
    assert body["recognized"] is True
    assert body["text"] == "你好小歪"
    assert observed == [(b"\x01\x01" * 100, 16000)]


def test_empty_uploaded_recording_result_is_not_reported_as_success(asr_client, monkeypatch):
    set_adapter(monkeypatch, "")
    response = asr_client.post("/api/setup/asr/test", data={
        "audio": (io.BytesIO(wav_bytes()), "sample.wav"),
    })
    assert response.status_code == 422
    assert response.get_json()["code"] == "asr_empty_transcript"
    assert response.get_json()["recognized"] is False


def test_silent_connection_probe_is_not_speech_acceptance(asr_client, monkeypatch):
    set_adapter(monkeypatch, "")
    body = asr_client.post("/api/setup/asr/test", json={}).get_json()
    assert body["ok"] is True
    assert body["test_kind"] == "silence_connectivity"
    assert body["recognized"] is False


@pytest.mark.parametrize("raw", [b"not-wav", wav_bytes(width=1), wav_bytes(rate=96000), wav_bytes()[:-4]])
def test_invalid_or_truncated_wav_never_reaches_provider(asr_client, monkeypatch, raw):
    observed = []
    set_adapter(monkeypatch, "", observed)
    response = asr_client.post("/api/setup/asr/test", data={
        "audio": (io.BytesIO(raw), "sample.wav"),
    })
    assert response.status_code == 400
    assert observed == []


def test_false_json_request_is_rejected_instead_of_becoming_silence(asr_client):
    assert asr_client.post("/api/setup/asr/test", json=False).status_code == 400
