"""R6 independent text tests: active config, no robot, no settings saves.

All credentials are synthetic and all persistence roots are temporary. The one
transport integration uses a loopback HTTP server through the real safe fetch.
The existing daily quota is expected to change; model/config/chat files are not.
"""
from __future__ import annotations

import json
import os
import socket
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from flask import Flask

from deskbot_server import config as config_module
from deskbot_server import device_data, env
from deskbot_server.llm import runtime
from deskbot_server.web.blueprints import app_bp, longshu_test_bp

FAKE_KEY = "r6-test-only-active-llm-key"
TEXT_ROUTE = "/api/longshu/llm/test"
STATUS_ROUTE = "/api/longshu/tests/status"


def _files(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture
def text_test_env(tmp_path, monkeypatch):
    local_dir = tmp_path / "data" / "local"
    local_dir.mkdir(parents=True)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_API_KEY=r6-test-only-system-key\n"
        "LLM_MODEL=system-model\n"
        "LLM_BASE_URL=https://unused.example.test/v1\n"
        "ASR_PROVIDER=openai_compatible\n"
        "ASR_ENDPOINT=https://speech.example.test/v1/audio/transcriptions\n"
        "ASR_MODEL=FunAudioLLM/Fun-ASR-Nano-2512\n"
        "ASR_API_KEY=r6-test-only-asr-key\n"
        "ASR_TIMEOUT_SECONDS=600\n"
        "ASR_RESPONSE_FORMAT=text\n"
        "ASR_STREAM=true\n"
        "TTS_PROVIDER=openai_compatible\n"
        "TTS_ENDPOINT=https://speech.example.test/v1/audio/speech\n"
        "TTS_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice\n"
        "TTS_VOICE=aiden\n"
        "TTS_API_KEY=r6-test-only-tts-key\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  model_name: yaml-model\n", encoding="utf-8")
    model_path = local_dir / "llm_models.json"
    active_entry = {
        "id": "active-test-model", "name": "Test active model",
        "model_name": "active-model", "protocol": "openai",
        "base_url": "https://active.example.test/v1", "api_key": FAKE_KEY,
    }
    model_path.write_text(json.dumps({
        "active_model_id": active_entry["id"], "models": [active_entry],
    }), encoding="utf-8")
    (local_dir / "chat-history-sentinel.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(env, "ENV_FILE", env_path)
    monkeypatch.setattr(env, "_last_signature", None)
    monkeypatch.setattr(env, "_file_managed_values", {})
    monkeypatch.setattr(device_data, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(device_data, "LOCAL_DATA_ROOT", local_dir)
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", config_path)
    quota_calls = []

    def consume_quota():
        quota_calls.append(True)
        return {"remaining": 49, "daily_limit": 50}, None

    monkeypatch.setattr(app_bp, "_consume_settings_test_quota", consume_quota)
    real_safe_open = runtime.safe_provider_urlopen

    def deny_network(*_args, **_kwargs):
        raise AssertionError("external provider request is forbidden in this test")

    monkeypatch.setattr(runtime, "safe_provider_urlopen", deny_network)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    app.register_blueprint(longshu_test_bp.bp)
    with patch.dict(os.environ, {"DESKBOT_SERVER_CONFIG": str(config_path)}, clear=True):
        yield {
            "client": app.test_client(), "root": tmp_path,
            "model_path": model_path, "active_entry": active_entry,
            "env_path": env_path, "quota_calls": quota_calls,
            "real_safe_open": real_safe_open,
        }


def test_text_uses_active_runtime_model_and_ignores_payload_overrides(
    text_test_env, monkeypatch
):
    from deskbot_server.application import tool_executor
    from deskbot_server.tts import gateway
    from deskbot_server.web.blueprints import proxy_bp

    def forbidden(*_args, **_kwargs):
        raise AssertionError("text-only test must not dispatch robot/tools/TTS or save config")

    monkeypatch.setattr(tool_executor, "execute_tools_sync", forbidden)
    monkeypatch.setattr(tool_executor, "execute_tools_async", forbidden)
    monkeypatch.setattr(gateway, "synthesize_gateway_tts", forbidden)
    monkeypatch.setattr(proxy_bp, "_forward", forbidden)
    monkeypatch.setattr(env, "update_env_keys", forbidden)
    monkeypatch.setattr(config_module, "save_config", forbidden)
    received = []
    # Even model output that looks like a tool request remains plain text.
    plain_reply = '{"tts":"测试回答","tools":[{"name":"move_head"}]}'

    def completion(messages, **kwargs):
        received.append((messages, kwargs))
        return plain_reply, {}

    monkeypatch.setattr(longshu_test_bp, "chat_completion", completion)
    before = _files(text_test_env["root"])
    response = text_test_env["client"].post(TEXT_ROUTE, json={
        "text": "  用一句话介绍自己  ", "device_id": "robot-must-not-be-contacted",
        "endpoint": "http://127.0.0.1:1/override", "base_url": "http://127.0.0.1:2",
        "api_key": "payload-must-not-be-used", "model": "payload-model",
        "model_name": "payload-model-2", "protocol": "ark_responses",
        "tools": [{"name": "move_head"}], "speak": True,
    })
    assert response.status_code == 200, response.json
    assert response.json["reply"] == plain_reply
    assert response.json["model"] == "active-model"
    assert len(received) == 1
    messages, kwargs = received[0]
    assert messages[-1] == {"role": "user", "content": "用一句话介绍自己"}
    assert [message["role"] for message in messages] == ["system", "user"]
    assert kwargs["config"].source == "device"
    assert kwargs["config"].api_key == FAKE_KEY
    assert kwargs["config"].api_base == "https://active.example.test/v1"
    assert kwargs["json_mode"] is False
    assert "device_id" not in kwargs and "on_tts_ready" not in kwargs
    assert text_test_env["quota_calls"] == [True]
    assert FAKE_KEY not in response.get_data(as_text=True)
    assert _files(text_test_env["root"]) == before


@pytest.mark.parametrize("payload", [None, [], {}, {"text": 42}, {"text": ""},
                                     {"text": " \n "}, {"text": "中" * 4001}])
def test_empty_or_invalid_text_is_rejected_before_provider_and_quota(text_test_env, payload):
    response = text_test_env["client"].post(TEXT_ROUTE, json=payload)
    assert response.status_code == 400
    assert response.json["ok"] is False
    assert text_test_env["quota_calls"] == []


def test_oversized_request_rejected_before_json_parse(text_test_env):
    response = text_test_env["client"].post(
        TEXT_ROUTE, data=b"x" * (32 * 1024 + 1), content_type="application/json"
    )
    assert response.status_code == 413
    assert text_test_env["quota_calls"] == []


@pytest.mark.parametrize("missing_key", ["", "请替换你的密钥"])
def test_missing_active_key_cannot_be_supplied_by_request(text_test_env, missing_key):
    entry = dict(text_test_env["active_entry"], api_key=missing_key)
    text_test_env["model_path"].write_text(json.dumps({
        "active_model_id": entry["id"], "models": [entry],
    }), encoding="utf-8")
    response = text_test_env["client"].post(
        TEXT_ROUTE, json={"text": "你好", "api_key": "payload-key-must-not-enable-test"}
    )
    assert response.status_code == 400
    assert "API Key" in response.json["error"]
    assert text_test_env["quota_calls"] == []


def test_missing_model_has_specific_configuration_error(text_test_env, monkeypatch):
    cfg = runtime.resolve_llm_config()
    monkeypatch.setattr(longshu_test_bp, "resolve_llm_config", lambda: replace(cfg, model=""))
    response = text_test_env["client"].post(TEXT_ROUTE, json={"text": "你好"})
    assert response.status_code == 400
    assert "模型" in response.json["error"]
    assert text_test_env["quota_calls"] == []


@pytest.mark.parametrize("reply", ["", " \n\t", None, []])
def test_provider_empty_reply_is_not_marked_success(text_test_env, monkeypatch, reply):
    monkeypatch.setattr(longshu_test_bp, "chat_completion", lambda *_a, **_k: (reply, {}))
    response = text_test_env["client"].post(TEXT_ROUTE, json={"text": "你好"})
    assert response.status_code == 502
    assert response.json["ok"] is False
    assert "没有文字回答" in response.json["error"]


@pytest.mark.parametrize("exception_type,status", [(ValueError, 400), (RuntimeError, 502)])
def test_provider_errors_redact_active_and_environment_credentials(
    text_test_env, monkeypatch, exception_type, status
):
    monkeypatch.setenv("EXTRA_API_TOKEN", "r6-test-only-other-token")

    def fail(*_args, **_kwargs):
        raise exception_type(
            f"upstream rejected {FAKE_KEY}; token r6-test-only-other-token; "
            "Authorization: Bearer r6-unknown-bearer-value; "
            'api_key="r6-unknown-key-value"; password=r6-unknown-password\nsecond line'
        )

    monkeypatch.setattr(longshu_test_bp, "chat_completion", fail)
    response = text_test_env["client"].post(TEXT_ROUTE, json={"text": "你好"})
    assert response.status_code == status
    error = response.json["error"]
    assert "upstream rejected" in error and "[redacted]" in error
    for secret in (FAKE_KEY, "r6-test-only-other-token", "r6-unknown-bearer-value",
                   "r6-unknown-key-value", "r6-unknown-password"):
        assert secret not in error
    assert "\n" not in error and len(error) <= 600


def test_status_cold_loads_shared_speech_readiness_without_save_or_provider(
    text_test_env, monkeypatch
):
    from deskbot_server import speech_status

    assert "ASR_API_KEY" not in os.environ and "TTS_API_KEY" not in os.environ
    before = _files(text_test_env["root"])
    original = speech_status.get_speech_config_status
    snapshots = []

    def shared_status():
        snapshot = original()
        snapshots.append(snapshot)
        return snapshot

    def deny_dns(*_args, **_kwargs):
        raise AssertionError("configuration status must not resolve/contact providers")

    monkeypatch.setattr(speech_status, "get_speech_config_status", shared_status)
    monkeypatch.setattr(socket, "getaddrinfo", deny_dns)
    response = text_test_env["client"].get(STATUS_ROUTE)
    assert response.status_code == 200, response.json
    result = response.json
    assert result["configuration_only"] is True
    assert len(snapshots) == 1
    for name in ("asr", "tts"):
        assert result[name]["configured"] is True
        assert result[name]["provider"] == "openai_compatible"
        assert result[name]["connection_status"] == "not_tested"
        for key, value in snapshots[0][name].items():
            assert result[name][key] == value
    assert result["tts"]["voice"] == "aiden"
    assert result["llm"]["configured"] is True
    assert result["llm"]["model"] == "active-model"
    assert result["llm"]["connection_status"] == "not_tested"
    body = response.get_data(as_text=True)
    assert FAKE_KEY not in body and "r6-test-only-asr-key" not in body
    assert "r6-test-only-tts-key" not in body
    assert text_test_env["quota_calls"] == []
    assert _files(text_test_env["root"]) == before


def test_text_route_real_local_http_request_is_plain_and_uses_active_config(
    text_test_env, monkeypatch
):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, dict(self.headers), payload))
            body = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "我是龙数小歪。"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("DESKBOT_PROVIDER_PRIVATE_ORIGINS", origin)
    monkeypatch.setattr(runtime, "safe_provider_urlopen", text_test_env["real_safe_open"])
    entry = dict(text_test_env["active_entry"], base_url=origin + "/v1")
    text_test_env["model_path"].write_text(json.dumps({
        "active_model_id": entry["id"], "models": [entry],
    }), encoding="utf-8")
    before = _files(text_test_env["root"])
    try:
        response = text_test_env["client"].post(TEXT_ROUTE, json={
            "text": "介绍一下自己", "api_key": "ignored-payload-key",
            "model": "ignored-model", "endpoint": "http://127.0.0.1:1/ignored",
        })
        assert response.status_code == 200, response.json
        assert response.json["reply"] == "我是龙数小歪。"
        assert len(calls) == 1
        path, headers, payload = calls[0]
        assert path == "/v1/chat/completions"
        assert headers["Authorization"] == "Bearer " + FAKE_KEY
        assert payload["model"] == "active-model" and payload["stream"] is False
        assert payload["messages"][-1] == {"role": "user", "content": "介绍一下自己"}
        assert "response_format" not in payload and "tools" not in payload
        assert _files(text_test_env["root"]) == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
