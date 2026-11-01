"""Doubao recording-file ASR: submit local WAV, then query the same task ID."""
from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from deskbot_server.core.concurrency import asr_infer_slot
from deskbot_server.core.ports.asr import (
    AsrAuthenticationError, AsrConfigurationError, AsrInputError,
    AsrProviderResponseError, AsrProviderUnavailableError, AsrRateLimitError,
)
from deskbot_server.env import load_dotenv
from deskbot_server.safe_fetch import safe_provider_urlopen
from deskbot_server.util import pcm_to_wav_bytes

DEFAULT_ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
DEFAULT_RESOURCE_ID = "volc.seedasr.auc"
MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FileAsrConfig:
    endpoint: str
    api_key: str = field(repr=False)
    resource_id: str
    timeout_seconds: float
    max_audio_bytes: int
    model: str = "bigmodel"


def resolve_file_asr_config(settings):
    load_dotenv()
    try:
        return FileAsrConfig(
            endpoint=os.environ.get("ASR_ENDPOINT") or DEFAULT_ENDPOINT,
            api_key=os.environ.get("ASR_API_KEY") or os.environ.get("VOLCENGINE_ASR_API_KEY") or "",
            resource_id=os.environ.get("VOLCENGINE_ASR_RESOURCE_ID") or DEFAULT_RESOURCE_ID,
            timeout_seconds=float(os.environ.get("ASR_TIMEOUT_SECONDS") or settings.timeout_seconds),
            max_audio_bytes=int(os.environ.get("ASR_MAX_AUDIO_BYTES") or settings.max_audio_bytes),
        )
    except (TypeError, ValueError):
        raise AsrConfigurationError("ASR 超时或音频大小设置无效。") from None


def validate_file_asr_config(config, *, require_key=True):
    if config.endpoint != DEFAULT_ENDPOINT:
        raise AsrConfigurationError("豆包文件识别请使用 https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit。")
    if config.resource_id not in {"volc.seedasr.auc", "volc.bigasr.auc"}:
        raise AsrConfigurationError("文件识别 Resource ID 应为 volc.seedasr.auc 或 volc.bigasr.auc。")
    if require_key and not config.api_key:
        raise AsrConfigurationError("请填写火山语音 ASR API Key。")
    if len(config.api_key) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in config.api_key):
        raise AsrConfigurationError("ASR API Key 格式无效。")
    if not math.isfinite(config.timeout_seconds) or not 1 <= config.timeout_seconds <= 600:
        raise AsrConfigurationError("ASR 超时必须在 1 到 600 秒之间。")
    if not 1024 <= config.max_audio_bytes <= 100 * 1024 * 1024:
        raise AsrConfigurationError("ASR 音频大小上限无效。")


def _post(url, headers, payload, timeout):
    request = urllib.request.Request(url, method="POST", headers=headers,
                                     data=json.dumps(payload).encode("utf-8"))
    try:
        with safe_provider_urlopen(request, timeout=timeout) as response:
            code = response.headers.get("X-Api-Status-Code", "")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        error = AsrAuthenticationError if status in {401, 403} else AsrRateLimitError if status == 429 else AsrProviderUnavailableError
        raise error(f"豆包文件识别请求失败（HTTP {status}），请检查凭证、资源权限与网络。", status_code=status) from None
    except (OSError, urllib.error.URLError):
        raise AsrProviderUnavailableError("无法连接豆包文件识别，请检查网络与 TLS 证书。") from None
    except ValueError:
        raise AsrConfigurationError("豆包文件识别地址未通过安全检查。") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AsrProviderResponseError("豆包文件识别响应超过大小上限。")
    # Never surface provider body/header messages: they can echo credentials.
    if code not in {"20000000", "20000001", "20000002", "20000003"}:
        safe_code = code if code.isdigit() and len(code) == 8 else "unknown"
        raise AsrProviderResponseError(f"豆包文件识别返回错误码 {safe_code}。")
    try:
        body = json.loads(raw or b"{}")
    except (ValueError, UnicodeError):
        raise AsrProviderResponseError("豆包文件识别返回了无效 JSON。") from None
    if not isinstance(body, dict):
        raise AsrProviderResponseError("豆包文件识别响应格式无效。")
    return code, body


class VolcengineFileAsrAdapter:
    def __init__(self, settings):
        self.settings = settings

    async def transcribe(self, pcm_bytes, sample_rate):
        config = resolve_file_asr_config(self.settings)
        validate_file_asr_config(config)
        if not pcm_bytes or len(pcm_bytes) % 2 or sample_rate not in {8000, 16000, 24000, 32000, 44100, 48000}:
            raise AsrInputError("请提供有效的单声道 16 位 PCM 录音。")
        if len(pcm_bytes) > config.max_audio_bytes:
            raise AsrInputError("录音超过 ASR 大小上限，请缩短后重试。")
        headers = {
            "Content-Type": "application/json", "x-api-key": config.api_key,
            "X-Api-Resource-Id": config.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()), "X-Api-Sequence": "-1",
        }
        payload = {
            "user": {"uid": "豆包语音"},
            "audio": {"data": base64.b64encode(pcm_to_wav_bytes(pcm_bytes, sample_rate)).decode("ascii"),
                      "format": "wav", "codec": "raw", "rate": sample_rate, "bits": 16, "channel": 1},
            "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": False,
                        "enable_ddc": False, "enable_speaker_info": False, "enable_channel_split": False,
                        "show_utterances": False, "vad_segment": False, "sensitive_words_filter": ""},
        }
        timeout = min(20, config.timeout_seconds)
        async with asr_infer_slot():
            try:
                async with asyncio.timeout(config.timeout_seconds):
                    code, _ = await asyncio.to_thread(_post, config.endpoint, headers, payload, timeout)
                    if code == "20000003":
                        return ""
                    if code != "20000000":
                        raise AsrProviderResponseError("豆包文件识别未确认任务提交成功。")
                    while True:
                        await asyncio.sleep(1)
                        code, body = await asyncio.to_thread(_post, config.endpoint.removesuffix("submit") + "query", headers, {}, timeout)
                        if code in {"20000001", "20000002"}:
                            continue
                        if code == "20000003":
                            return ""
                        result = body.get("result")
                        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                            raise AsrProviderResponseError("豆包文件识别成功响应缺少识别文字。")
                        return result["text"].strip()
            except TimeoutError:
                raise AsrProviderUnavailableError("豆包文件识别超过总等待时限，请稍后重试。") from None
