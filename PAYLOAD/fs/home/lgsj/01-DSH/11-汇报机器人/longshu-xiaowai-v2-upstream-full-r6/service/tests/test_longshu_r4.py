from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from deskbot_server.infrastructure.asr.openai_compat import (
    OpenAiCompatibleAsrAdapter, OpenAiCompatibleAsrConfig, _multipart_body,
    parse_transcription_response, validate_openai_compatible_asr_config,
)
from deskbot_server.core.ports.asr import AsrProviderResponseError
from deskbot_server.tts import gateway

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("streaming", [False, True])
def test_real_livekit_tts_uses_qwen_and_reloads_voice(monkeypatch, streaming):
    pytest.importorskip("livekit.agents")
    from livekit.agents import APIConnectOptions
    from deskbot_server import rtc_livekit_plugins
    monkeypatch.setenv("TTS_PROVIDER", "openai_compatible")
    monkeypatch.setenv("TTS_API_KEY", "fake-private-key")
    monkeypatch.setenv("TTS_VOICE", "aiden")
    monkeypatch.setattr(gateway, "load_dotenv", lambda **kw: None)
    calls = []
    def fake_request(text, config):
        calls.append((text, config.speaker))
        return b'\x01\x00' * 3200
    monkeypatch.setattr(gateway, "_request_tts", fake_request)
    async def run():
        speech = rtc_livekit_plugins._build_tts()
        assert speech.provider == "deskbot-openai-compatible"
        for voice in ("aiden", "vivian"):
            monkeypatch.setenv("TTS_VOICE", voice)
            options = APIConnectOptions(max_retry=0)
            if streaming:
                output = speech.stream(conn_options=options)
                output.push_text("你好，")
                output.push_text("我是龙数小歪。")
                output.end_input()
            else:
                output = speech.synthesize("你好，我是龙数小歪。", conn_options=options)
            async with output:
                frames = [event.frame async for event in output]
            assert frames and all(f.sample_rate == 16000 and f.num_channels == 1 for f in frames)
            assert sum(len(f.data) for f in frames) > 0
        await speech.aclose()
    asyncio.run(run())
    assert calls == [("你好，我是龙数小歪。", "aiden"), ("你好，我是龙数小歪。", "vivian")]


def test_real_livekit_asr_routes_to_gateway(monkeypatch):
    pytest.importorskip("livekit.agents")
    from livekit import rtc
    from livekit.agents import APIConnectOptions
    from deskbot_server import rtc_livekit_plugins
    monkeypatch.setenv("ASR_PROVIDER", "openai_compatible")
    monkeypatch.setenv("ASR_API_KEY", "fake-private-key")
    monkeypatch.setenv("ASR_ENDPOINT", "https://modelhub.lgdg.cc/aigateway/v1/audio/transcriptions")
    monkeypatch.setenv("ASR_MODEL", "FunAudioLLM/Fun-ASR-Nano-2512")
    monkeypatch.setenv("ASR_STREAM", "true")
    monkeypatch.setenv("ASR_RESPONSE_FORMAT", "text")
    seen = []
    def fake_open(req, timeout):
        seen.append(req)
        return io.BytesIO('data: {"text":"你好龙数小歪"}\n\ndata: [DONE]\n\n'.encode())
    monkeypatch.setattr("deskbot_server.infrastructure.asr.openai_compat.safe_provider_urlopen", fake_open)
    async def run():
        speech = rtc_livekit_plugins._build_stt()
        from deskbot_server.rtc_barge_in import PlaybackEchoGuard
        speech.bind_playback_echo_guard(PlaybackEchoGuard())
        audio = rtc.AudioFrame(data=b'\x40\x04' * 16000, sample_rate=16000, num_channels=1, samples_per_channel=16000)
        event = await speech._recognize_impl(audio, conn_options=APIConnectOptions(max_retry=0))
        assert event.alternatives[0].text == "你好龙数小歪"
        await speech.aclose()
    asyncio.run(run())
    assert len(seen) == 1
    assert seen[0].full_url.endswith('/audio/transcriptions')
    assert b'FunAudioLLM/Fun-ASR-Nano-2512' in seen[0].data


def wav_bytes(rate=24000, channels=1, width=2):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        sample = b"\x80" if width == 1 else (1000).to_bytes(width, "little", signed=True)
        wav.writeframes(sample * (rate // 10) * channels)
    return buffer.getvalue()


@pytest.mark.parametrize("raw,format,expected", [
    ('你好小歪', 'text', '你好小歪'),
    ('12345', 'text', '12345'),
    ('{"text":"你好"}', 'json', '你好'),
    ('data: {"type":"transcript.text.delta","delta":"你"}\n\ndata: {"delta":"好"}\n\ndata: [DONE]\n\n', 'text', '你好'),
    ('data: {"delta":"你"}\n\ndata: {"type":"transcript.text.done","text":"你好"}\n\n', 'text', '你好'),
    ('data: {"text":"你"}\n\ndata: {"text":"你好"}\n\n', 'text', '你好'),
    ('event: transcript\r\ndata: 你\r\n\r\ndata: 好\r\n\r\n', 'text', '你好'),
    ('data: {"choices":[{"delta":{"content":"你好"}}]}\n\ndata: [DONE]', 'text', '你好'),
    ('', 'text', ''),
])
def test_asr_response_formats(raw, format, expected):
    assert parse_transcription_response(raw.encode(), response_format=format) == expected


@pytest.mark.parametrize("raw", [b'{"error":"bad key"}', b'data: {"error":"bad"}\n\n', b'event: error\ndata: nope\n\n', b'<html>bad gateway</html>', b'\xff', b'data: {bad\n\n'])
def test_asr_errors_not_transcripts(raw):
    with pytest.raises(AsrProviderResponseError):
        parse_transcription_response(raw, response_format="text")


def test_asr_multipart_matches_user_contract():
    body, boundary = _multipart_body(wav_bytes(), model="FunAudioLLM/Fun-ASR-Nano-2512",
        language="auto", response_format="text", stream=True)
    assert b'name="stream"\r\n\r\ntrue' in body
    assert b'name="response_format"\r\n\r\ntext' in body
    assert b'name="language"' not in body
    assert b'Content-Type: audio/wav' in body
    assert boundary.encode() in body


def test_asr_accepts_600_seconds_and_text_sse(monkeypatch):
    config = OpenAiCompatibleAsrConfig("https://modelhub.lgdg.cc/aigateway/v1/audio/transcriptions",
        "FunAudioLLM/Fun-ASR-Nano-2512", "test-key", "auto", 600, 1024 * 1024, "text", True)
    validate_openai_compatible_asr_config(config)
    class Response(io.BytesIO):
        pass
    captured = {}
    def fake_open(req, timeout):
        captured.update(url=req.full_url, body=req.data, auth=req.get_header("Authorization"), timeout=timeout)
        return Response(b'data: {"delta":"hello"}\n\ndata: [DONE]\n\n')
    monkeypatch.setattr("deskbot_server.infrastructure.asr.openai_compat.safe_provider_urlopen", fake_open)
    assert OpenAiCompatibleAsrAdapter._transcribe_sync(b'\0\0' * 160, 16000, config) == 'hello'
    assert captured['timeout'] == 600 and captured['auth'] == 'Bearer test-key'
    assert captured['url'] == config.endpoint


@pytest.mark.parametrize("rate,channels,width", [(24000,1,2),(48000,2,2),(16000,1,2),(22050,1,1),(32000,2,3)])
def test_wav_conversion(rate, channels, width):
    pcm = gateway.wav_to_pcm(wav_bytes(rate,channels,width))
    assert abs(len(pcm) - 3200) <= 4
    assert not pcm.startswith(b'RIFF')
    assert len(pcm) % 2 == 0


@pytest.mark.parametrize("raw", [b'{"error":"denied"}', b'RIFF\x00\x00\x00\x00WAVE', wav_bytes()[:-20]])
def test_invalid_wav_rejected(raw):
    with pytest.raises(ValueError):
        gateway.wav_to_pcm(raw)


def test_tts_request_and_pcm_callback(monkeypatch):
    captured = {}
    def fake_open(req, timeout):
        captured.update(url=req.full_url, payload=json.loads(req.data), authorization=req.get_header('Authorization'), accept=req.get_header('Accept'))
        return io.BytesIO(wav_bytes())
    monkeypatch.setattr(gateway, 'safe_provider_urlopen', fake_open)
    chunks = []
    config = gateway.GatewayTtsConfig(api_key='test-secret', speaker='vivian')
    result = asyncio.run(gateway.synthesize_gateway_tts('你好', config, on_pcm=chunks.append))
    assert captured['payload'] == {'model':gateway.MODEL,'input':'你好','voice':'vivian','response_format':'wav'}
    assert captured['url'] == gateway.ENDPOINT
    assert captured['authorization'] == 'Bearer test-secret'
    assert captured['accept'] == 'audio/wav'
    assert b''.join(chunks) == result.pcm and result.sample_rate == 16000
    assert 'test-secret' not in repr(config)


def test_gateway_settings_key_preservation_and_voice(monkeypatch, tmp_path):
    from deskbot_server import env
    test_env = tmp_path / '.env'
    test_env.write_text('TTS_API_KEY=old-secret\nLLM_MODEL=keep-llm\n', encoding='utf-8')
    monkeypatch.setattr(env, 'ENV_FILE', test_env)
    monkeypatch.setattr(env, '_last_signature', None)
    monkeypatch.setattr(env, '_file_managed_values', {})
    with patch.dict(os.environ, {}, clear=True):
        result = gateway.save_config({'speaker':'dylan','api_key':''})
        assert result['speaker'] == 'dylan' and result['api_key_set']
        assert 'old-secret' not in json.dumps(result)
        assert 'LLM_MODEL=keep-llm' in test_env.read_text()
        assert 'TTS_API_KEY=old-secret' in test_env.read_text()
        gateway.save_config({'speaker':'aiden','api_key':'********'})
        assert 'TTS_API_KEY=old-secret' in test_env.read_text()
        assert gateway.save_config({'clear_api_key':True})['api_key_set'] is False


@pytest.mark.parametrize('voice', gateway.VOICES)
def test_all_nine_voices(voice):
    gateway.validate_config(gateway.GatewayTtsConfig(api_key='test-key', speaker=voice))


@pytest.mark.parametrize('change', [{'speaker':'unknown'},{'endpoint':'http://127.0.0.1/speech'},{'api_key':'bad\nkey'},{'timeout_seconds':float('nan')},{'timeout_seconds':601}])
def test_invalid_tts_config(change):
    with pytest.raises(ValueError):
        gateway.validate_config(replace(gateway.GatewayTtsConfig(api_key='test'), **change))


def test_rtc_uses_gateway_credentials_without_doubao(monkeypatch):
    from deskbot_server.rtc_agent_sdk import RtcAgentSdkManager
    from deskbot_server.core.settings import AppSettings
    monkeypatch.setattr(gateway, 'load_dotenv', lambda: False)
    with patch.dict(os.environ, {'ASR_API_KEY':'asr-test','TTS_API_KEY':'tts-test','TTS_PROVIDER':'openai_compatible'}, clear=True):
        manager = RtcAgentSdkManager(AppSettings.from_config({}))
        assert manager._resolve_speech_credentials()[0] == 'deskbot_api_key'


def test_camera_longshu_profile_keeps_v1_capture_baseline():
    import configparser
    ini = configparser.ConfigParser(interpolation=None)
    ini.read(ROOT / 'hardware/platformio.ini')
    flags = ini['env:longshu_xiaowai_full']['build_flags']
    for flag in ['FB_COUNT=1','GRAB_LATEST=0','PSRAM_DMA=0','XCLK_HZ=20000000','NATIVE_TUNING=1']:
        assert '-DDESKBOT_CAMERA_' + flag in flags
    assert 'FLASH' not in flags
    camera = (ROOT/'hardware/firmware/camera.cpp').read_text()
    assert 'private JPEG allocation failed; dropping frame' in camera


def test_no_stale_web_firmware_payload():
    from deskbot_server.firmware_update import bundled_manifest
    manifest = json.loads((ROOT/'service/src/deskbot_server/firmware_payload/manifest.json').read_text())
    if manifest.get('build_required'):
        assert bundled_manifest() is None


def test_longshu_private_gateway_origin_is_explicit_and_exact(monkeypatch):
    from deskbot_server.safe_fetch import validate_provider_http_url
    env_example = (ROOT / 'service/.env.example').read_text(encoding='utf-8')
    expected = 'DESKBOT_PROVIDER_PRIVATE_ORIGINS=https://modelhub.lgdg.cc'
    assert expected in env_example
    monkeypatch.setenv('DESKBOT_PROVIDER_PRIVATE_ORIGINS', 'https://modelhub.lgdg.cc')
    parsed = validate_provider_http_url('https://modelhub.lgdg.cc/aigateway/v1/models')
    assert parsed.hostname == 'modelhub.lgdg.cc'
    with pytest.raises(ValueError, match='non-public'):
        validate_provider_http_url('https://172.16.120.252/aigateway/v1/models')


def test_rtc_paths_and_ui_wired():
    plugins = (ROOT/'service/src/deskbot_server/rtc_livekit_plugins.py').read_text()
    assert 'self._adapter = build_asr_adapter(' in plugins
    assert 'synthesize_gateway_tts as synthesize_doubao_tts' in plugins
    js = (ROOT/'service/src/deskbot_server/web/static/gateway_tts.js').read_text()
    assert '/api/gateway/tts/test' in js
    for voice in gateway.VOICES:
        assert voice in js


def test_templates_compile():
    import jinja2
    templates = ROOT/'service/src/deskbot_server/web/templates'
    environment = jinja2.Environment(loader=jinja2.FileSystemLoader(templates))
    for path in templates.rglob('*.html'):
        environment.get_template(str(path.relative_to(templates)))


def test_gateway_api_status_save_and_test(monkeypatch, tmp_path):
    from flask import Flask
    from deskbot_server import env
    from deskbot_server.web.blueprints import gateway_bp, app_bp
    monkeypatch.setattr(env, 'ENV_FILE', tmp_path/'.env')
    monkeypatch.setattr(env, '_last_signature', None)
    monkeypatch.setattr(env, '_file_managed_values', {})
    monkeypatch.setattr(app_bp, '_consume_settings_test_quota', lambda: ({}, None))
    async def synthesize(text, config):
        assert config.speaker == 'sohee'
        return gateway.GatewayTtsResult(b'\0\0'*160,16000,2)
    monkeypatch.setattr(gateway_bp, 'synthesize_gateway_tts', synthesize)
    app = Flask(__name__)
    app.register_blueprint(gateway_bp.bp)
    with patch.dict(os.environ, {}, clear=True):
        client = app.test_client()
        resp = client.post('/api/gateway/tts', json={'api_key':'private-test-key','speaker':'sohee'})
        assert resp.status_code == 200
        assert 'private-test-key' not in resp.text
        resp = client.get('/api/gateway/tts')
        assert resp.json['config']['api_key_set']
        assert len(resp.json['config']['voices']) == 9
        resp = client.post('/api/gateway/tts/test', json={'text':'你好'})
        assert resp.status_code == 200 and resp.json['wav_base64'].startswith('UklGR')
        assert client.post('/api/gateway/tts', json={'speaker':'invalid'}).status_code == 400
        assert client.post('/api/gateway/tts/test', json={'text':'a'*301}).status_code == 400


def test_green_model_page_and_voice_route(monkeypatch):
    from deskbot_server.web.app import create_app
    monkeypatch.setenv('TTS_PROVIDER','openai_compatible')
    client = create_app().test_client()
    for url in ['/advanced', '/voice']:
        response = client.get(url)
        assert response.status_code == 200
        assert '龙数小歪' in response.text
        assert 'longshu_green.css' in response.text
        assert 'gateway_tts.js' in response.text
        assert 'Qwen 语音合成' in response.text
        assert 'gk_' not in response.text
    assert client.post('/api/gateway/tts',json={'speaker':'aiden'},headers={'Origin':'https://evil.example'}).status_code == 403
