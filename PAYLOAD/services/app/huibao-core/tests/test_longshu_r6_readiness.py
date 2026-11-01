from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture()
def fresh_preset(monkeypatch, tmp_path):
    from deskbot_server import env

    for key in tuple(os.environ):
        if key.startswith(("ASR_", "TTS_", "DOUBAO_", "VOLCENGINE_", "SEED_TTS_", "BYTEPLUS_SEED_")):
            monkeypatch.delenv(key)
    preset = tmp_path / ".env"
    contents = (
        "ASR_PROVIDER=openai_compatible\n"
        "ASR_ENDPOINT=https://speech.example.com/v1/audio/transcriptions\n"
        "ASR_MODEL=test-asr\nASR_API_KEY=private-asr-fixture\n"
        "TTS_PROVIDER=openai_compatible\n"
        "TTS_ENDPOINT=https://speech.example.com/v1/audio/speech\n"
        "TTS_MODEL=test-tts\nTTS_VOICE=aiden\nTTS_API_KEY=private-tts-fixture\n"
    )
    preset.write_text(contents)
    for line in contents.splitlines():
        # Register env-loader side effects with pytest's rollback machinery.
        key = line.split("=", 1)[0]
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    config = tmp_path / "config.yaml"
    config.write_text("asr:\n  provider: doubao_streaming\ntts:\n  provider: doubao\n")
    monkeypatch.setenv("DESKBOT_SERVER_CONFIG", str(config))
    monkeypatch.setattr(env, "ENV_FILE", preset)
    monkeypatch.setattr(env, "_last_signature", None)
    monkeypatch.setattr(env, "_file_managed_values", {})
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **kw: pytest.fail("readiness must not probe a provider"))
    return preset


def test_first_read_activates_preset_without_save_or_provider_requests(fresh_preset):
    from flask import Flask
    from deskbot_server.web.blueprints.gateway_bp import bp

    before = fresh_preset.read_bytes()
    app = Flask(__name__)
    app.register_blueprint(bp)
    response = app.test_client().get("/api/setup/speech")
    assert response.status_code == 200
    status = response.json
    assert status["configured"] is True and status["needs_config"] is False
    assert status["asr"]["provider"] == status["tts"]["provider"] == "openai_compatible"
    assert status["voice_clone_required"] is False
    assert status["connection_status"] == "not_tested"
    assert status["asr"]["connection_status"] == status["tts"]["connection_status"] == "not_tested"
    assert "private-asr-fixture" not in response.text
    assert "private-tts-fixture" not in response.text
    assert fresh_preset.read_bytes() == before


@pytest.mark.parametrize("field", ["ASR_API_KEY", "TTS_API_KEY"])
def test_only_the_current_missing_speech_provider_is_reported(fresh_preset, monkeypatch, field):
    from deskbot_server.speech_status import get_speech_config_status

    monkeypatch.setenv(field, "")
    status = get_speech_config_status()
    missing = "asr" if field.startswith("ASR") else "tts"
    other = "tts" if missing == "asr" else "asr"
    assert status["needs_config"]
    assert not status[missing]["configured"]
    assert status[other]["configured"]
    assert "豆包" not in status[missing]["message"]


def test_switching_to_doubao_really_checks_doubao_credentials(fresh_preset, monkeypatch):
    from deskbot_server.speech_status import get_speech_config_status
    from deskbot_server.tts.doubao import DoubaoTtsConfig

    monkeypatch.setenv("TTS_PROVIDER", "doubao")
    monkeypatch.setattr("deskbot_server.speech_status.load_doubao_tts_config", lambda: DoubaoTtsConfig(api_key=""))
    status = get_speech_config_status()
    assert status["asr"]["configured"]
    assert not status["tts"]["configured"]
    assert "DOUBAO_TTS_API_KEY" in status["tts"]["message"]


def test_key_alone_does_not_make_an_invalid_voice_ready(fresh_preset, monkeypatch):
    from deskbot_server.speech_status import get_speech_config_status

    monkeypatch.setenv("TTS_VOICE", "not-a-supported-voice")
    status = get_speech_config_status()
    assert status["asr"]["configured"]
    assert not status["tts"]["configured"]
    assert "音色" in status["tts"]["message"]


def test_status_uses_actual_independent_timeouts(fresh_preset, monkeypatch):
    from deskbot_server.speech_status import get_speech_config_status

    monkeypatch.setenv("ASR_TIMEOUT_SECONDS", "123.5")
    monkeypatch.setenv("TTS_TIMEOUT_SECONDS", "456")
    status = get_speech_config_status()
    assert status["asr"]["timeout_seconds"] == 123.5
    assert status["tts"]["timeout_seconds"] == 456
    assert status["configured"]


@pytest.mark.parametrize("value", ["nan", "inf", "0", "601"])
def test_invalid_tts_timeout_is_not_reported_as_ready(fresh_preset, monkeypatch, value):
    from deskbot_server.speech_status import SpeechConfigurationError, get_speech_config_status

    monkeypatch.setenv("TTS_TIMEOUT_SECONDS", value)
    with pytest.raises(SpeechConfigurationError):
        get_speech_config_status()


@pytest.mark.parametrize("path", ["/api/setup/speech", "/api/longshu/tests/status"])
@pytest.mark.parametrize("failure", ["missing_config", "malformed_yaml", "asr_timeout", "tts_timeout"])
def test_invalid_configuration_returns_safe_json_error(fresh_preset, monkeypatch, failure, path):
    from flask import Flask
    from deskbot_server.web.blueprints.gateway_bp import bp
    from deskbot_server.web.blueprints.longshu_test_bp import bp as tests_bp

    config_path = Path(os.environ["DESKBOT_SERVER_CONFIG"])
    if failure == "missing_config":
        config_path.unlink()
    elif failure == "malformed_yaml":
        config_path.write_text("asr: [private-malformed-config")
    else:
        monkeypatch.setenv(failure.upper() + "_SECONDS", "private-malformed-config")
    app = Flask(__name__)
    app.register_blueprint(bp)
    app.register_blueprint(tests_bp)
    response = app.test_client().get(path)
    assert response.status_code == 400
    assert response.json["ok"] is False
    assert response.json["error"]
    assert "private-malformed-config" not in response.text
    assert "private-asr-fixture" not in response.text
    assert "private-tts-fixture" not in response.text


def test_gateway_audition_does_not_save_or_send_draft_credentials():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    script = Path(__file__).parents[1] / "src/deskbot_server/web/static/gateway_tts.js"
    js = r"""
const fs=require('fs'); global.window=global;
eval(fs.readFileSync(process.argv[1],'utf8'));
const vm=Object.assign(LongshuTtsUI.data(), LongshuTtsUI.methods);
vm.tts.api_key='unsaved-test-key';
vm.saveTtsConfig=()=>{throw new Error('audition must never save');};
vm.gatewayRequest=async (url, body)=>{
  if(url!='/api/gateway/tts/test') throw new Error('unexpected request');
  if(Object.keys(body).join()!=='text') throw new Error('draft config leaked');
  return {wav_base64:'AAAAAA==',elapsed_ms:10};
};
(async()=>{await vm.testGatewayTts();
if(!vm.tts.result.ok || !vm.tts.audioUrl) throw new Error(JSON.stringify(vm.tts.result));
URL.revokeObjectURL(vm.tts.audioUrl);
})().catch(e=>{console.error(e);process.exit(1);});
"""
    result = subprocess.run([node, "-e", js, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("llm_missing,speech_missing,expected", [(False, False, ""), (True, False, "LLM"), (False, True, "Qwen TTS")])
def test_global_banner_uses_active_provider_readiness(llm_missing, speech_missing, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    template = (Path(__file__).parents[1] / "src/deskbot_server/web/templates/base_2c.html").read_text()
    start = template.index("  (function(){\n    document.addEventListener('DOMContentLoaded',function(){\n      var box=document.getElementById('globalSetup')")
    js = template[start:template.index("</script>", start)]
    js = js.replace("{{ url_for(\"app2c.advanced\") }}", "/advanced")
    js = js.replace("{{ url_for('app2c.devices') }}", "/devices")
    payloads = {
        "/api/setup/llm": {"needs_config": llm_missing},
        "/app/api/devices": {"devices": [{"device_id": "robot"}], "current_device_id": "robot"},
        "/api/setup/speech": {"needs_config": speech_missing, "asr": {"configured": True}, "tts": {"configured": not speech_missing, "provider_name": "Qwen TTS", "message": "请填写 TTS API Key。"}},
    }
    runner = "const payloads=" + json.dumps(payloads, ensure_ascii=False) + ";\n" + r"""
const box={hidden:true,innerHTML:'',getAttribute:()=>''};
global.window={location:{href:'/'},DeskbotConsole:{apiJson:async url=>{
  if(!(url in payloads)) throw new Error('unexpected URL '+url); return payloads[url];
}}};
global.document={getElementById:()=>box,addEventListener:(name,fn)=>fn()};
""" + js + "\nsetImmediate(()=>process.stdout.write(box.innerHTML));"
    result = subprocess.run([node, "-e", runner], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "豆包" not in result.stdout
    if expected:
        assert expected in result.stdout
    else:
        assert not result.stdout
