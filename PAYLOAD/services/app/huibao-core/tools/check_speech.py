r"""Check this source checkout's TTS and ASR, without a robot or running server.

Windows CMD: .venv\Scripts\python.exe tools\check_speech.py
Optional: --audio recording.wav (ASR only), --tts-only.
Reads this service/.env and config.yaml; does not edit either configuration.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

SERVICE_ROOT = Path(__file__).resolve().parents[1]
TEST_TEXT = "你好，我是龙数小歪，现在测试语音识别。"


def _secrets() -> list[str]:
    return sorted(
        {value for key, value in os.environ.items()
         if value and any(word in key.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))},
        key=len, reverse=True,
    )


def _safe_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(str(value))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "<invalid endpoint>"
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return "<invalid endpoint>"


def _safe_text(value: object) -> str:
    text = str(value)
    for secret in _secrets():
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"https?://[^\s<>\"']+", lambda match: _safe_endpoint(match.group()), text)
    text = re.sub(r"(?i)\bBearer\s+[^\s\"';,}]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|password|secret)([\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]+",
        r"\1\2[redacted]", text,
    )
    return " ".join(text.split())[:700]


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "运行依赖缺失，请先用本目录的 setup_full_windows.ps1 安装环境。"
    if isinstance(exc, TimeoutError):
        return "请求超过等待时限，请检查网络或网关。"
    return _safe_text(exc) or type(exc).__name__


def _pin_source_environment(service_root: Path) -> None:
    """Do not accidentally diagnose an inherited R3/R4 client profile."""
    os.environ.update({
        "DESKBOT_PROJECT_ROOT": str(service_root),
        "DESKBOT_ENV_FILE": str(service_root / ".env"),
        "DESKBOT_SERVER_CONFIG": str(service_root / "config.yaml"),
        "DESKBOT_DATA_DIR": str(service_root / "data"),
        "DESKBOT_MODELS_DIR": str(service_root / "models"),
    })
    # This is an isolated diagnostic process. Speech settings come from this
    # checkout, rather than inherited values from a previously launched client.
    for key in list(os.environ):
        if key.startswith(("ASR_", "TTS_", "DESKBOT_PROVIDER_")):
            os.environ.pop(key, None)
    sys.path.insert(0, str(service_root / "src"))


def _load_runtime(service_root: Path) -> SimpleNamespace:
    _pin_source_environment(service_root)
    import deskbot_server
    import yaml
    from deskbot_server.env import load_dotenv
    from deskbot_server.core.settings import AppSettings
    from deskbot_server.infrastructure.asr import openai_compat
    from deskbot_server.tts import gateway

    module_path = Path(deskbot_server.__file__).resolve()
    if not module_path.is_relative_to(service_root / "src"):
        raise RuntimeError("加载到了其他版本的服务，请使用 R6 自身 .venv 重新运行。")
    load_dotenv(force_reload=True)
    # ASR only needs its own YAML section: unrelated LLM prompt files must not
    # prevent independent speech diagnostics from running.
    try:
        config = yaml.safe_load((service_root / "config.yaml").read_text(encoding="utf-8-sig")) or {}
        if not isinstance(config, dict):
            raise ValueError("config root must be a mapping")
    except (OSError, ValueError, yaml.YAMLError):
        raise RuntimeError("本目录 config.yaml 无法读取或格式无效。") from None
    return SimpleNamespace(gateway=gateway, asr=openai_compat, settings=AppSettings, config=config)


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    if sample_rate != 16000 or not pcm or len(pcm) % 2:
        raise ValueError("TTS 未产生有效的 16 kHz 单声道 PCM16 音频。")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(pcm)


def _stage() -> dict:
    return {"status": "SKIP", "elapsed_ms": 0, "configuration": {}, "error": ""}


async def _check(runtime: SimpleNamespace, output_dir: Path, *, audio: Path | None, tts_only: bool) -> dict:
    report = {"schema_version": 1, "ok": False, "tts": _stage(), "asr": _stage()}
    pcm = None
    rate = 16000
    tts = report["tts"]
    if audio is not None:
        print("\n[1/2] TTS：SKIP（--audio 仅测试 ASR）")
    else:
        print("\n[1/2] TTS：请求合成固定中文句子")
        started = time.monotonic()
        try:
            # A failed request must not leave a previous successful test's WAV
            # looking like the output of this run.
            (output_dir / "tts.wav").unlink(missing_ok=True)
            cfg = runtime.gateway.load_gateway_tts_config()
            tts["configuration"] = {
                "endpoint_set": bool(cfg.endpoint), "model_set": bool(cfg.model),
                "voice_set": bool(cfg.speaker), "api_key_set": bool(cfg.api_key),
            }
            print(f"  endpoint: {_safe_text(_safe_endpoint(cfg.endpoint))}")
            print(f"  model: {_safe_text(cfg.model)}; voice: {_safe_text(cfg.speaker)}")
            print(f"  API Key: {'已设置' if cfg.api_key else '未设置'}")
            result = await runtime.gateway.synthesize_gateway_tts(TEST_TEXT, cfg)
            _write_wav(output_dir / "tts.wav", result.pcm, result.sample_rate)
            pcm, rate = result.pcm, result.sample_rate
            tts["status"] = "PASS"
            print(f"  PASS：音频保存至 {output_dir / 'tts.wav'}；请试听确认人声。")
        except Exception as exc:
            tts.update(status="FAIL", error=_safe_error(exc))
            print(f"  FAIL：{tts['error']}")
        tts["elapsed_ms"] = round((time.monotonic() - started) * 1000)

    asr = report["asr"]
    if tts_only:
        print("\n[2/2] ASR：SKIP（--tts-only）")
    else:
        print("\n[2/2] ASR：上传 WAV 音频识别")
        started = time.monotonic()
        try:
            settings = runtime.settings.from_config({"asr": runtime.config.get("asr", {})}).asr
            cfg = runtime.asr.resolve_openai_compatible_asr_config(settings)
            asr["configuration"] = {
                "endpoint_set": bool(cfg.endpoint), "model_set": bool(cfg.model),
                "api_key_set": bool(cfg.api_key),
            }
            print(f"  endpoint: {_safe_text(_safe_endpoint(cfg.endpoint))}")
            print(f"  model: {_safe_text(cfg.model)}; format: {_safe_text(cfg.response_format)}; stream: {bool(cfg.stream)}")
            print(f"  API Key: {'已设置' if cfg.api_key else '未设置'}")
            if audio is not None:
                if audio.stat().st_size > runtime.gateway.MAX_WAV_BYTES:
                    raise ValueError("输入 WAV 超过 32 MiB 上限。")
                pcm = runtime.gateway.wav_to_pcm(audio.read_bytes(), 16000)
                rate = 16000
                print("  使用 --audio 指定的 WAV（转换为 16 kHz 单声道 PCM16）。")
            if pcm is None:
                asr["error"] = "TTS 失败，没有可上传音频；使用 --audio recording.wav 可独立检查 ASR。"
                print(f"  SKIP：{asr['error']}")
            else:
                transcript = await runtime.asr.OpenAiCompatibleAsrAdapter(settings).transcribe(pcm, rate)
                if not isinstance(transcript, str) or not transcript.strip():
                    raise ValueError("ASR 返回空识别结果，不能判定通过；请使用清晰人声 WAV 再测。")
                asr["status"] = "PASS"
                print(f"  PASS：接口返回非空文本：{_safe_text(transcript)}")
                print("  请核对识别文字；非空结果不代表识别准确率或机器人麦克风已验收。")
        except Exception as exc:
            asr.update(status="FAIL", error=_safe_error(exc))
            print(f"  FAIL：{asr['error']}")
        asr["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    report["ok"] = (audio is not None or tts["status"] == "PASS") and (tts_only or asr["status"] == "PASS")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, help="仅检查 ASR，上传指定 WAV，跳过 TTS")
    parser.add_argument("--tts-only", action="store_true", help="仅检查 TTS")
    args = parser.parse_args(argv)
    if args.audio is not None and args.tts_only:
        parser.error("--audio 与 --tts-only 不能同时使用")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    # Provider logs may contain third-party response bodies. Only the explicitly
    # sanitized status output below is needed by this standalone command.
    logging.disable(logging.CRITICAL)
    print(f"龙数小歪 R6 语音自检；当前服务源码：{SERVICE_ROOT}")
    print("配置来源：本目录 .env + config.yaml；无需启动网页或连接机器人。")
    output_dir = SERVICE_ROOT / "data" / "local" / "speech-check"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        runtime = _load_runtime(SERVICE_ROOT)
        report = asyncio.run(_check(runtime, output_dir, audio=args.audio, tts_only=args.tts_only))
    except Exception as exc:
        error = _safe_error(exc)
        report = {"schema_version": 1, "ok": False, "tts": _stage(), "asr": _stage(), "error": error}
        print(f"自检启动失败：{error}")
    report_path = output_dir / "report.json"
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n诊断报告：{report_path}")
        print("报告不含密钥、音频、识别文字或完整配置值，可提供此报告排查。")
    except OSError:
        print("无法保存诊断报告，请检查本目录写入权限。")
        return 1
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
