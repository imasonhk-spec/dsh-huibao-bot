"""Independent, read-only configuration checks and plain text LLM testing.

No robot session, tools, TTS, configuration writes or conversation persistence
are triggered by a text test. Provider credentials stay in the PC process.
"""
from __future__ import annotations

import os
import re
import time

from flask import Blueprint, jsonify, request

from deskbot_server.env import load_dotenv
from deskbot_server.llm.runtime import chat_completion, resolve_llm_config

bp = Blueprint("longshu_tests", __name__)
MAX_TEXT_LENGTH = 4000


def _safe_error(exc: Exception, api_key: str = "") -> str:
    message = str(exc)
    secrets = {api_key} if api_key else set()
    # Do not replace short ordinary environment values inside model names.
    secrets.update(value for name, value in os.environ.items()
                   if len(value) >= 8 and any(part in name.upper()
                   for part in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")))
    for value in sorted(secrets, key=len, reverse=True):
        message = message.replace(value, "[redacted]")
    message = re.sub(r"(?i)\bBearer\s+[^\s\"';,}]+", "Bearer [redacted]", message)
    message = re.sub(r"(?i)(api[_-]?key|access[_-]?token|password|secret)([\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]+",
                     r"\1\2[redacted]", message)
    return " ".join(message.split())[:600] or "LLM 请求失败，请检查服务日志。"


def llm_test_status() -> dict:
    try:
        load_dotenv()
        config = resolve_llm_config()
        configured = bool(config.model.strip() and config.api_key.strip()
                          and "请替换" not in config.api_key)
        return {"configured": configured, "model": config.model,
                "provider": config.protocol, "source": config.source,
                "timeout_seconds": 60, "connection_status": "not_tested",
                "message": "LLM 已配置，可直接发送问题。" if configured
                else "请先在模型配置中填写 LLM 模型和 API Key。"}
    except (ValueError, OSError):
        return {"configured": False, "model": "", "timeout_seconds": 60,
                "connection_status": "not_tested",
                "message": "LLM 配置不完整，请打开模型配置检查。"}


@bp.get("/api/longshu/tests/status")
def test_status():
    from deskbot_server.speech_status import SpeechConfigurationError, get_speech_config_status
    try:
        status = get_speech_config_status()
    except SpeechConfigurationError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    asr = dict(status["asr"])
    tts = dict(status["tts"])
    tts["voice"] = tts.get("speaker", "")
    asr.setdefault("timeout_seconds", 600)
    tts.setdefault("timeout_seconds", 600)
    return jsonify(ok=True, asr=asr, llm=llm_test_status(), tts=tts,
                   configuration_only=True)


@bp.post("/api/longshu/llm/test")
def text_question():
    # Bound the complete request before Flask parses JSON, not just text.
    if request.content_length and request.content_length > 32 * 1024:
        return jsonify(ok=False, error="问题请求过大，请缩短文本。"), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        return jsonify(ok=False, error="请输入文字问题。"), 400
    prompt = payload["text"].strip()
    if not prompt or len(prompt) > MAX_TEXT_LENGTH:
        return jsonify(ok=False, error="问题须为 1–4000 个字符。"), 400
    config = None
    try:
        load_dotenv()
        config = resolve_llm_config()
        if not config.api_key.strip() or "请替换" in config.api_key:
            return jsonify(ok=False, error="请先配置 LLM API Key。"), 400
        if not config.model.strip():
            return jsonify(ok=False, error="请先选择 LLM 模型。"), 400
        from deskbot_server.web.blueprints.app_bp import _consume_settings_test_quota
        quota, error = _consume_settings_test_quota()
        if error:
            return error
        started = time.monotonic()
        reply, _meta = chat_completion(
            [{"role": "system", "content": "你是龙数惠宝。请直接用简洁中文回答问题，不需要 JSON 格式。"},
             {"role": "user", "content": prompt}],
            config=config, json_mode=False, temperature=0.7,
        )
        if not isinstance(reply, str) or not reply.strip():
            return jsonify(ok=False, error="LLM 已返回响应，但没有文字回答，请检查模型。"), 502
        return jsonify(ok=True, reply=reply.strip(), model=config.model,
                       elapsed_ms=round((time.monotonic() - started) * 1000), quota=quota)
    except ValueError as exc:
        return jsonify(ok=False, error=_safe_error(exc, config.api_key if config else "")), 400
    except Exception as exc:
        return jsonify(ok=False, error=_safe_error(exc, config.api_key if config else "")), 502
