"""Unrecoverable SDK errors must not leave a connected but silent voice agent."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_internal_close_event_stops_poller_and_disconnects_room(monkeypatch):
    agents = pytest.importorskip("livekit.agents")
    from livekit.agents.voice.events import CloseEvent, CloseReason
    from deskbot_server import rtc_livekit_plugins as plugins

    async def start_without_network(self, *args, **kwargs):
        pass

    # Keep the real event emitter/init but don't connect a room or load models.
    monkeypatch.setattr(agents.AgentSession, "__init__", agents.AgentSession.__init__)
    monkeypatch.setattr(agents.AgentSession, "start", start_without_network)
    monkeypatch.setattr(agents.AgentSession, "aclose", agents.AgentSession.aclose)
    monkeypatch.setattr(plugins, "_apply_deskbot_room_audio_options", lambda *a: None)
    monkeypatch.setattr(plugins, "_deskbot_start_instruction_poller", lambda *a: None)
    plugins._install_agent_session_defaults()

    async def run():
        session = agents.AgentSession()
        room = SimpleNamespace(disconnect=AsyncMock())
        await session.start(room=room)
        poller = asyncio.create_task(asyncio.Event().wait())
        session._deskbot_instruction_poller = poller
        # This is the SDK's internal close path; public aclose() isn't called.
        event = CloseEvent(reason=CloseReason.ERROR)
        session.emit("close", event)
        session.emit("close", event)
        await session._deskbot_close_room_task
        await asyncio.gather(poller, return_exceptions=True)
        room.disconnect.assert_awaited_once()
        assert poller.cancelled()
        assert session._deskbot_instruction_poller is None
        assert session._deskbot_agent_state == "closed"

    asyncio.run(run())
