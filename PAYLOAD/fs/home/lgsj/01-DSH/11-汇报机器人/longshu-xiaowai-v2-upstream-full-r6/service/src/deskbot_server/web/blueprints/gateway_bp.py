"""Local-only speech synthesis settings and audible connection test."""
import asyncio
import base64
from dataclasses import replace
from flask import Blueprint, jsonify, request
from deskbot_server.tts.gateway import config_status, load_gateway_tts_config, save_config, synthesize_gateway_tts
from deskbot_server.util import pcm_to_wav_bytes

bp = Blueprint("gateway_setup", __name__)


@bp.get("/api/setup/speech")
def get_speech_status():
    from deskbot_server.speech_status import SpeechConfigurationError, get_speech_config_status
    try:
        return jsonify(ok=True, **get_speech_config_status())
    except SpeechConfigurationError as exc:
        return jsonify(ok=False, error=str(exc)), 400


@bp.get("/api/gateway/tts")
def get_config():
    return jsonify(ok=True, config=config_status())


@bp.post("/api/gateway/tts")
def update_config():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="请求体必须是 JSON 对象。"), 400
    try:
        return jsonify(ok=True, config=save_config(payload))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="TTS 配置无效，请检查服务地址、模型、音色及超时。"), 400


@bp.post("/api/gateway/tts/test")
def test_speech():
    from deskbot_server.web.blueprints.app_bp import _consume_settings_test_quota
    quota, error = _consume_settings_test_quota()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(ok=False, error="请求体必须是 JSON 对象。"), 400
    text = str(payload.get("text") or "你好，我是龙数惠宝，很高兴认识你。")
    if len(text) > 300:
        return jsonify(ok=False, error="试听文本不能超过 300 字。"), 400
    try:
        config = load_gateway_tts_config()
        # Legacy preview callers may choose a voice for one audition. Provider
        # endpoint/model/credentials always come from the saved PC config.
        if payload.get("speaker"):
            config = replace(config, speaker=str(payload["speaker"]).strip())
        result = asyncio.run(synthesize_gateway_tts(text, config))
        wav = pcm_to_wav_bytes(result.pcm, result.sample_rate)
        return jsonify(ok=True, wav_base64=base64.b64encode(wav).decode("ascii"),
                       sample_rate=result.sample_rate, elapsed_ms=result.elapsed_ms, quota=quota,
                       provider="openai_compatible", speaker=config.speaker,
                       pcm_total_bytes=len(result.pcm))
    except (RuntimeError, ValueError) as exc:
        return jsonify(ok=False, error=str(exc)), 502
    except TimeoutError:
        return jsonify(ok=False, error="TTS 请求超时。"), 504
