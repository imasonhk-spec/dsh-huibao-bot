from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import logging
import os
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "check_speech.py"
spec = importlib.util.spec_from_file_location("check_speech", SCRIPT)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def runtime(*, tts_error=None, transcript="你好，我是龙数小歪，现在测试语音识别。", asr_error=None):
    calls = []
    pcm = b"\x01\x01" * 1600
    tts_config = SimpleNamespace(
        endpoint="https://speech.example.test/v1/audio/speech?token=private-query",
        model="qwen-test", speaker="aiden", api_key="private-test-key",
    )
    asr_config = SimpleNamespace(
        endpoint="https://speech.example.test/v1/audio/transcriptions",
        model="fun-asr-test", api_key="private-test-key", response_format="text", stream=True,
    )

    async def synthesize(text, config):
        calls.append(("tts", text))
        if tts_error:
            raise tts_error
        return SimpleNamespace(pcm=pcm, sample_rate=16000)

    class Asr:
        def __init__(self, settings):
            pass

        async def transcribe(self, input_pcm, sample_rate):
            calls.append(("asr", input_pcm, sample_rate))
            if asr_error:
                raise asr_error
            return transcript

    def wav_to_pcm(raw, target_rate):
        with wave.open(io.BytesIO(raw), "rb") as audio:
            assert target_rate == audio.getframerate() == 16000
            return audio.readframes(audio.getnframes())

    return SimpleNamespace(
        gateway=SimpleNamespace(
            load_gateway_tts_config=lambda: tts_config,
            synthesize_gateway_tts=synthesize,
            MAX_WAV_BYTES=32 * 1024 * 1024,
            wav_to_pcm=wav_to_pcm,
        ),
        asr=SimpleNamespace(
            resolve_openai_compatible_asr_config=lambda settings: asr_config,
            OpenAiCompatibleAsrAdapter=Asr,
        ),
        settings=SimpleNamespace(from_config=lambda config: SimpleNamespace(asr=config["asr"])),
        config={"asr": {}}, calls=calls, pcm=pcm,
    )


def test_speech_check_uploads_exact_tts_pcm_and_keeps_report_private(tmp_path, capsys):
    current = runtime()
    report = asyncio.run(diagnostic._check(current, tmp_path, audio=None, tts_only=False))
    assert report["ok"] is True
    assert current.calls == [("tts", diagnostic.TEST_TEXT), ("asr", current.pcm, 16000)]
    with wave.open(str(tmp_path / "tts.wav"), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
        assert audio.readframes(audio.getnframes()) == current.pcm
    serialized = json.dumps(report, ensure_ascii=False)
    for forbidden in ("private-test-key", "speech.example.test", diagnostic.TEST_TEXT, "qwen-test"):
        assert forbidden not in serialized
    assert report["tts"]["configuration"]["api_key_set"] is True
    output = capsys.readouterr().out
    assert "private-test-key" not in output
    assert "private-query" not in output


@pytest.mark.parametrize("transcript", ["", "   ", None])
def test_empty_asr_cannot_pass(tmp_path, transcript):
    current = runtime(transcript=transcript)
    report = asyncio.run(diagnostic._check(current, tmp_path, audio=None, tts_only=False))
    assert report["ok"] is False
    assert report["tts"]["status"] == "PASS"
    assert report["asr"]["status"] == "FAIL"
    assert "空识别" in report["asr"]["error"]


def test_external_audio_checks_asr_without_tts_dependency(tmp_path):
    current = runtime(tts_error=RuntimeError("TTS HTTP 502"))
    recording = tmp_path / "recording.wav"
    independent_pcm = b"\x21\x00" * 3200
    diagnostic._write_wav(recording, independent_pcm, 16000)
    report = asyncio.run(diagnostic._check(current, tmp_path, audio=recording, tts_only=False))
    assert report["tts"]["status"] == "SKIP"
    assert report["asr"]["status"] == "PASS"
    assert report["ok"] is True
    assert current.calls == [("asr", independent_pcm, 16000)]


def test_tts_failure_without_audio_skips_asr(tmp_path):
    (tmp_path / "tts.wav").write_bytes(b"previous successful run")
    current = runtime(tts_error=RuntimeError("TTS HTTP 502"))
    report = asyncio.run(diagnostic._check(current, tmp_path, audio=None, tts_only=False))
    assert report["ok"] is False
    assert report["asr"]["status"] == "SKIP"
    assert [call[0] for call in current.calls] == ["tts"]
    assert not (tmp_path / "tts.wav").exists()


def test_tts_only_does_not_request_asr(tmp_path):
    current = runtime()
    report = asyncio.run(diagnostic._check(current, tmp_path, audio=None, tts_only=True))
    assert report["ok"] is True
    assert report["asr"]["status"] == "SKIP"
    assert [call[0] for call in current.calls] == ["tts"]


def test_errors_redact_known_keys_bearer_and_query(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ASR_API_KEY", "private-test-key")
    error = RuntimeError(
        'request https://user:password@example.test/x?token=hidden failed: '
        'private-test-key Authorization: Bearer undisclosed-secret api_key="unknown-value"'
    )
    current = runtime(asr_error=error)
    report = asyncio.run(diagnostic._check(current, tmp_path, audio=None, tts_only=False))
    output = json.dumps(report) + capsys.readouterr().out
    assert report["asr"]["status"] == "FAIL"
    for forbidden in ("private-test-key", "hidden", "undisclosed-secret", "unknown-value", "password@"):
        assert forbidden not in output


def test_default_paths_ignore_old_checkout_profile(tmp_path):
    old_environment = {
        "DESKBOT_ENV_FILE": "C:/old-r4/service/.env", "DESKBOT_PROJECT_ROOT": "C:/old-r4/service",
        "ASR_API_KEY": "old-key", "TTS_ENDPOINT": "https://old.invalid",
        "DESKBOT_PROVIDER_PRIVATE_ORIGINS": "https://old.invalid", "PATH": os.environ.get("PATH", ""),
    }
    with patch.dict(os.environ, old_environment, clear=True), patch.object(diagnostic.sys, "path", []):
        diagnostic._pin_source_environment(tmp_path)
        assert os.environ["DESKBOT_ENV_FILE"] == str(tmp_path / ".env")
        assert os.environ["DESKBOT_SERVER_CONFIG"] == str(tmp_path / "config.yaml")
        assert os.environ["DESKBOT_PROJECT_ROOT"] == str(tmp_path)
        assert "ASR_API_KEY" not in os.environ
        assert "TTS_ENDPOINT" not in os.environ
        assert "DESKBOT_PROVIDER_PRIVATE_ORIGINS" not in os.environ


@pytest.mark.parametrize("transcript,expected_exit", [("你好", 0), ("", 1)])
def test_main_returns_nonzero_on_failure_and_saves_report(tmp_path, monkeypatch, transcript, expected_exit):
    current = runtime(transcript=transcript)
    monkeypatch.setattr(diagnostic, "SERVICE_ROOT", tmp_path)
    monkeypatch.setattr(diagnostic, "_load_runtime", lambda root: current)
    monkeypatch.setattr(logging, "disable", lambda level: None)
    assert diagnostic.main([]) == expected_exit
    report = json.loads((tmp_path / "data" / "local" / "speech-check" / "report.json").read_text())
    assert report["ok"] == (expected_exit == 0)
