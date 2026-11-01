"""Read-only speech setup status for the providers used by the live pipeline.

Loading the shipped PC configuration is sufficient; visiting/saving a settings
form is never an activation step. This status does not call a provider or claim
that credentials, network reachability, microphones or speakers were tested.
"""
from __future__ import annotations

import math
import os

import yaml

from deskbot_server.asr.env_store import get_asr_config_status
from deskbot_server.config import load_config
from deskbot_server.core.ports.asr import AsrConfigurationError
from deskbot_server.core.settings import AppSettings
from deskbot_server.env import load_dotenv
from deskbot_server.safe_fetch import validate_provider_websocket_url
from deskbot_server.tts.doubao import SESSION_TIMEOUT_SECONDS, load_doubao_tts_config
from deskbot_server.tts.gateway import config_status as gateway_config_status
from deskbot_server.tts.gateway import MING_MODEL, MOSS_MODEL


class SpeechConfigurationError(ValueError):
    """Public, credential-free error when readiness cannot be determined."""


def get_speech_config_status(settings: AppSettings | None = None) -> dict:
    """Return readiness, or a safe error without exposing malformed config."""
    try:
        return _read_speech_config_status(settings)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError, AsrConfigurationError):
        raise SpeechConfigurationError(
            "无法读取语音配置，请检查本机 config.yaml、ASR/TTS 参数及超时值。"
        ) from None


def _read_speech_config_status(settings: AppSettings | None = None) -> dict:
    # Do this before AppSettings: both ASR and TTS providers may come from .env.
    load_dotenv()
    current = settings or AppSettings.from_config(
        load_config(os.environ.get("DESKBOT_SERVER_CONFIG"))
    )
    raw_asr = get_asr_config_status(current)
    asr_provider = raw_asr["provider"]
    asr = {
        "provider": asr_provider,
        "provider_name": {
            "openai_compatible": "语音识别（兼容接口）",
            "doubao_streaming": "豆包流式 ASR",
            "funasr": "本地 FunASR",
        }.get(asr_provider, asr_provider),
        "configured": bool(raw_asr.get("configured")),
        "message": raw_asr.get("message", ""),
        "model": raw_asr.get("model", ""),
        "timeout_seconds": raw_asr.get("timeout_seconds") or current.asr.timeout_seconds,
    }
    provider = (os.environ.get("TTS_PROVIDER") or current.tts.provider).strip().lower()
    if provider == "openai_compatible":
        raw_tts = gateway_config_status()
        if not math.isfinite(raw_tts["timeout_seconds"]) or not 1 <= raw_tts["timeout_seconds"] <= 600:
            raise ValueError("invalid TTS timeout")
        tts = {
            "provider": provider,
            "provider_name": {MING_MODEL: "Ming TTS", MOSS_MODEL: "MOSS TTS"}.get(raw_tts["model"], "Qwen TTS"),
            "configured": raw_tts["configured"],
            "message": raw_tts["message"],
            "model": raw_tts["model"],
            "speaker": raw_tts["speaker"],
            "speaker_label": raw_tts.get("speaker_label", raw_tts["speaker"]),
            "timeout_seconds": raw_tts["timeout_seconds"],
        }
    elif provider == "doubao":
        cfg = load_doubao_tts_config()
        message = "豆包 TTS 已配置。"
        configured = bool(cfg.api_key)
        if not configured:
            message = "请填写当前豆包 TTS 的 DOUBAO_TTS_API_KEY。"
        try:
            validate_provider_websocket_url(cfg.ws_url, resolve_dns=False)
        except ValueError:
            configured = False
            message = "豆包 TTS 地址无效，请检查配置。"
        tts = {
            "provider": provider,
            "provider_name": "豆包 TTS",
            "configured": configured,
            "message": message,
            "model": cfg.model,
            "speaker": cfg.speaker,
            "timeout_seconds": SESSION_TIMEOUT_SECONDS,
        }
    else:
        tts = {
            "provider": provider,
            "provider_name": provider or "TTS",
            "configured": False,
            "message": "TTS_PROVIDER 不受支持，请选择 openai_compatible 或 doubao。",
            "model": "",
            "speaker": "",
            "timeout_seconds": None,
        }
    for part in (asr, tts):
        part["configuration_status"] = "configured" if part["configured"] else "unconfigured"
        part["connection_status"] = "not_tested"
    configured = asr["configured"] and tts["configured"]
    return {
        "configured": configured,
        "needs_config": not configured,
        "configuration_only": True,
        "connection_status": "not_tested",
        "asr": asr,
        "tts": tts,
        # Voice cloning is an optional separate feature, never an onboarding gate.
        "voice_clone_required": False,
    }
