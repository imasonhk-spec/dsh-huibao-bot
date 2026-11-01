"""Operator API for global ASR configuration and connectivity testing."""

from __future__ import annotations

import asyncio
import audioop
import base64
import binascii
import io
import os
import wave

from flask import Blueprint, jsonify, request

from deskbot_server.asr.env_store import get_asr_config_status, save_asr_env
from deskbot_server.config import load_config
from deskbot_server.core.ports.asr import AsrError
from deskbot_server.core.settings import AppSettings
from deskbot_server.infrastructure.asr.factory import build_asr_adapter
from deskbot_server.web.blueprints.app_bp import _consume_settings_test_quota

bp = Blueprint("asr_setup", __name__)
MAX_TEST_AUDIO_BYTES = 10 * 1024 * 1024


def _runtime_settings() -> AppSettings:
    config_path = os.environ.get("DESKBOT_SERVER_CONFIG")
    return AppSettings.from_config(load_config(config_path))


@bp.get("/api/setup/asr")
def setup_asr_get():
    return jsonify(
        {
            "ok": True,
            "scope": "global",
            "config": get_asr_config_status(_runtime_settings()),
        }
    )


@bp.post("/api/setup/asr")
@bp.patch("/api/setup/asr")
def setup_asr_update():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求体必须是 JSON 对象。"}), 400
    try:
        config = save_asr_env(payload)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError:
        return jsonify({"ok": False, "error": "保存 ASR 配置失败。"}), 500
    return jsonify({"ok": True, "config": config})


def _test_pcm(payload: dict) -> tuple[bytes, int]:
    try:
        sample_rate = int(payload.get("sample_rate") or 16000)
    except (TypeError, ValueError) as exc:
        raise ValueError("sample_rate 必须是整数。") from exc
    if sample_rate < 8000 or sample_rate > 48000:
        raise ValueError("sample_rate 必须在 8000 到 48000 Hz 之间。")
    encoded = str(payload.get("pcm_base64") or "").strip()
    if not encoded:
        # A one-second silent clip verifies DNS/TLS/authentication/model access
        # without storing or shipping a user's microphone sample.
        return b"\x00\x00" * sample_rate, sample_rate
    if len(encoded) > 14 * 1024 * 1024:
        raise ValueError("ASR 测试音频过大。")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("pcm_base64 不是有效的 Base64。") from exc
    if not pcm or len(pcm) % 2:
        raise ValueError("测试音频必须是非空的 s16le PCM。")
    if len(pcm) > MAX_TEST_AUDIO_BYTES:
        raise ValueError("ASR 测试音频不能超过 10 MiB。")
    return pcm, sample_rate


def _test_wav(upload) -> tuple[bytes, int]:
    """Validate a bounded WAV upload; keep speech samples only in memory."""
    raw = upload.read(MAX_TEST_AUDIO_BYTES + 1)
    if len(raw) > MAX_TEST_AUDIO_BYTES:
        raise ValueError("ASR 测试 WAV 不能超过 10 MiB。")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            channels = wav.getnchannels()
            rate = wav.getframerate()
            if wav.getsampwidth() != 2 or channels not in {1, 2} or wav.getcomptype() != "NONE":
                raise ValueError("请上传 16 位 PCM WAV（单声道或双声道），不支持压缩音频。")
            if not 8000 <= rate <= 48000:
                raise ValueError("WAV 采样率必须在 8000 到 48000 Hz 之间。")
            frames = wav.getnframes()
            pcm = wav.readframes(frames)
            if not pcm or len(pcm) != frames * channels * 2:
                raise ValueError("WAV 音频为空或文件不完整。")
    except (wave.Error, EOFError) as exc:
        raise ValueError("文件不是可读取的 PCM WAV，请先转换为 16 位 WAV。") from exc
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    return pcm, rate


def _asr_error_status(exc: AsrError) -> int:
    if exc.code == "asr_rate_limited":
        return 429
    if exc.code == "asr_provider_unavailable":
        return 503
    if exc.code in {"asr_invalid_response", "asr_error"}:
        return 502
    return 400


@bp.post("/api/setup/asr/test")
def setup_asr_test():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求体必须是 JSON 对象。"}), 400
    quota, limit_error = _consume_settings_test_quota()
    if limit_error:
        return limit_error
    try:
        upload = request.files.get("audio")
        is_speech_test = upload is not None or bool(payload.get("pcm_base64"))
        pcm, sample_rate = _test_wav(upload) if upload is not None else _test_pcm(payload)
        adapter = build_asr_adapter(_runtime_settings())
        text = asyncio.run(adapter.transcribe(pcm, sample_rate))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "quota": quota}), 400
    except AsrError as exc:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(exc),
                    "code": exc.code,
                    "retryable": exc.retryable,
                    "provider_status": exc.status_code,
                    "quota": quota,
                }
            ),
            _asr_error_status(exc),
        )
    except Exception:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "ASR 测试发生未预期错误，请查看脱敏后的服务日志。",
                    "code": "asr_test_failed",
                    "quota": quota,
                }
            ),
            500,
        )
    if is_speech_test and not text.strip():
        return jsonify({
            "ok": False,
            "error": "ASR 请求已完成，但录音没有识别出文字。请用清晰的人声 WAV 重试。",
            "code": "asr_empty_transcript",
            "test_kind": "speech_recognition",
            "recognized": False,
            "quota": quota,
        }), 422
    return jsonify(
        {
            "ok": True,
            "text": text,
            "test_kind": "speech_recognition" if is_speech_test else "silence_connectivity",
            "recognized": bool(text.strip()) if is_speech_test else False,
            "message": (
                "ASR 静音请求已完成；这只测试接口连通，请上传人声 WAV 验证识别。"
                if not is_speech_test
                else "ASR 录音识别成功。"
            ),
            "quota": quota,
        }
    )
