import asyncio
import base64
import io
import json
import urllib.error
import wave
from types import SimpleNamespace

import pytest

from deskbot_server.core.ports.asr import AsrAuthenticationError, AsrConfigurationError, AsrProviderResponseError, AsrProviderUnavailableError
from deskbot_server.infrastructure.asr import volcengine_file as mod


@pytest.fixture
def adapter(monkeypatch):
    config = mod.FileAsrConfig(mod.DEFAULT_ENDPOINT, "unit-test-key", mod.DEFAULT_RESOURCE_ID, 10, 100000)
    monkeypatch.setattr(mod, "resolve_file_asr_config", lambda _: config)
    async def no_wait(_):
        pass
    monkeypatch.setattr(mod.asyncio, "sleep", no_wait)
    return mod.VolcengineFileAsrAdapter(None)


def test_submit_query_wav_and_unique_ids(adapter, monkeypatch):
    calls = []
    def post(url, headers, payload, timeout):
        calls.append((url, headers, payload))
        if url.endswith("submit"):
            return "20000000", {}
        if len(calls) == 2:
            return "20000001", {}
        return "20000000", {"result": {"text": " 你好小歪 "}}
    monkeypatch.setattr(mod, "_post", post)
    pcm = b"\x10\x00" * 1600
    assert asyncio.run(adapter.transcribe(pcm, 16000)) == "你好小歪"
    assert len(calls) == 3 and calls[1][0].endswith("query")
    assert calls[0][1]["X-Api-Request-Id"] == calls[1][1]["X-Api-Request-Id"]
    body = calls[0][2]
    assert "url" not in body["audio"]
    assert body["request"]["enable_itn"] and not body["request"]["enable_punc"]
    assert body["user"]["uid"] == "豆包语音"
    with wave.open(io.BytesIO(base64.b64decode(body["audio"]["data"]))) as wav:
        assert wav.getframerate() == 16000 and wav.readframes(1600) == pcm
    asyncio.run(adapter.transcribe(pcm, 16000))
    assert calls[3][1]["X-Api-Request-Id"] != calls[0][1]["X-Api-Request-Id"]


@pytest.mark.parametrize("code,body,error", [
    ("20000003", {}, None),
    ("20000000", {}, AsrProviderResponseError),
    ("20000000", {"result": {"text": 123}}, AsrProviderResponseError),
])
def test_silence_and_malformed_results(adapter, monkeypatch, code, body, error):
    monkeypatch.setattr(mod, "_post", lambda url, *_: ("20000000", {}) if url.endswith("submit") else (code, body))
    if error:
        with pytest.raises(error):
            asyncio.run(adapter.transcribe(b"\0\0" * 100, 16000))
    else:
        assert asyncio.run(adapter.transcribe(b"\0\0" * 100, 16000)) == ""


def test_timeout_is_actionable(adapter, monkeypatch):
    def timed_out(*_):
        raise TimeoutError()
    monkeypatch.setattr(mod, "_post", timed_out)
    with pytest.raises(AsrProviderUnavailableError):
        asyncio.run(adapter.transcribe(b"\0\0" * 100, 16000))


def test_http_errors_do_not_echo_secrets(monkeypatch):
    def denied(*_, **__):
        raise urllib.error.HTTPError(mod.DEFAULT_ENDPOINT, 403, "unit-test-key", {}, io.BytesIO(b"unit-test-key"))
    monkeypatch.setattr(mod, "safe_provider_urlopen", denied)
    with pytest.raises(AsrAuthenticationError) as error:
        mod._post(mod.DEFAULT_ENDPOINT, {}, {}, 1)
    assert "unit-test-key" not in str(error.value)


def test_provider_errors_and_response_limits(monkeypatch):
    class Response:
        headers = {"X-Api-Status-Code": "unit-test-key"}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, size): return json.dumps({"error": "unit-test-key"}).encode()
    monkeypatch.setattr(mod, "safe_provider_urlopen", lambda *_, **__: Response())
    with pytest.raises(AsrProviderResponseError) as error:
        mod._post(mod.DEFAULT_ENDPOINT, {}, {}, 1)
    assert "unit-test-key" not in str(error.value)
    monkeypatch.setattr(Response, "read", lambda _, size: b"x" * size)
    with pytest.raises(AsrProviderResponseError, match="大小上限"):
        mod._post(mod.DEFAULT_ENDPOINT, {}, {}, 1)


def test_endpoint_and_resource_must_match_file_protocol():
    for endpoint, resource in [("https://other.example/submit", mod.DEFAULT_RESOURCE_ID), (mod.DEFAULT_ENDPOINT, "volc.seedasr.sauc.duration")]:
        with pytest.raises(AsrConfigurationError):
            mod.validate_file_asr_config(mod.FileAsrConfig(endpoint, "key", resource, 10, 4096))
