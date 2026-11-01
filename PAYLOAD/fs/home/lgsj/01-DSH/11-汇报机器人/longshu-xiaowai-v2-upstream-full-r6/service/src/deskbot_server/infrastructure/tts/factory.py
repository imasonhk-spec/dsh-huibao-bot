from __future__ import annotations

from deskbot_server.core.ports.tts import TtsPort
from deskbot_server.core.settings import AppSettings
from deskbot_server.infrastructure.tts.doubao_phoneme import DoubaoPhonemeTtsAdapter
from deskbot_server.tts.gateway import GatewayPhonemeTtsAdapter, gateway_enabled


class RuntimeTtsRouter:
    def __init__(self, settings):
        self.default_provider = settings.tts.provider
        self.gateway = GatewayPhonemeTtsAdapter()
        self.doubao = DoubaoPhonemeTtsAdapter(settings)

    async def synthesize_phoneme_segments(self, text):
        adapter = self.gateway if gateway_enabled(self.default_provider) else self.doubao
        return await adapter.synthesize_phoneme_segments(text)


def build_tts_adapter(settings: AppSettings) -> TtsPort:
    """Share the selected TTS backend between text, PB and runtime callers."""
    return RuntimeTtsRouter(settings)
