"""Temporary, device-scoped browser ownership of the existing RTC audio path."""
from __future__ import annotations

import asyncio
import base64
import secrets
import time
import uuid


class TicketBook:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.tickets = {}

    def issue(self, device_id):
        now = self.clock()
        self.tickets = {k: v for k, v in self.tickets.items() if v[1] > now}
        if len(self.tickets) >= 256:
            raise ValueError('browser_ticket_busy')
        token = secrets.token_urlsafe(32)
        self.tickets[token] = (device_id, now + 60)
        return token

    def consume(self, token):
        if not isinstance(token, str) or len(token) > 128:
            raise PermissionError('invalid_browser_ticket')
        row = self.tickets.pop(token, None)
        if row is None or row[1] <= self.clock():
            raise PermissionError('invalid_browser_ticket')
        return row[0]


class BrowserAudioLease:
    def __init__(self, bridge, device_id, binding):
        self.bridge, self.device_id, self.binding = bridge, device_id, binding
        self.output = asyncio.Queue(maxsize=100)
        self.failed = asyncio.Event()
        self.closed = False
        self.source_lock = asyncio.Lock()
        self.speech_lock = asyncio.Lock()
        self.playback_generation = 0
        self.manual_playback = False

    async def feed(self, pcm):
        async with self.source_lock:
            if self.closed or self.failed.is_set():
                raise ValueError('browser_audio_closed')
            if (self.bridge.binding_for(self.device_id) is not self.binding
                    or not self.binding.device_session.is_ready):
                raise ValueError('device_not_connected')
            if not isinstance(pcm, bytes) or not 2 <= len(pcm) <= 6400 or len(pcm) % 2:
                raise ValueError('invalid_pcm')
            await asyncio.to_thread(self.bridge.store.feed, self.device_id, pcm, 16000, 1)
            dialogue = getattr(self.binding, '_dialogue', None)
            if dialogue is not None and dialogue.gate.busy:
                return
            if not await self.binding.rtc_session.feed_pcm16(pcm, voice_active=True):
                raise ValueError('rtc_audio_unavailable')

    def send_audio(self, pcm, sample_rate, channels, *, manual=False):
        if self.manual_playback and not manual:
            return True
        if self.closed or self.failed.is_set():
            return False
        if sample_rate != 16000 or channels != 1:
            self.failed.set()
            return False
        try:
            self.output.put_nowait(bytes(pcm))
        except asyncio.QueueFull:
            self.failed.set()
            return False
        return True

    async def play_tts(self, chat, text):
        async with self.speech_lock:
            dialogue = getattr(self.binding, '_dialogue', None)
            if dialogue is None:
                from deskbot_server.application.meeting_voice import binding_for
                if binding_for(self.device_id):
                    from deskbot_server.application.core_dialogue import dialogue_for
                    dialogue = dialogue_for(self.binding)
            turn_id = str(uuid.uuid4())
            if dialogue:
                admitted = await dialogue.dispatch({'action':'begin','turn_id':turn_id})
                if not admitted['admitted']:
                    raise ValueError('dialogue_busy')
            else:
                self.clear()
            generation = self.playback_generation
            self.manual_playback = True
            try:
                rate, segments = await chat.tts_phoneme_segments(text)
                pcm = b''.join(segment.get('pcm', b'') for segment in segments)
                if not pcm:
                    raise ValueError('empty_tts_audio')
                if rate != 16000:
                    from livekit import rtc
                    resampler = rtc.AudioResampler(input_rate=rate, output_rate=16000, num_channels=1)
                    frames = resampler.push(rtc.AudioFrame(data=pcm, sample_rate=rate,
                                                          num_channels=1, samples_per_channel=len(pcm)//2))
                    frames.extend(resampler.flush())
                    pcm = b''.join(bytes(frame.data) for frame in frames)
                for offset in range(0, len(pcm), 640):
                    if self.closed or self.failed.is_set() or generation != self.playback_generation:
                        return False
                    chunk = pcm[offset:offset+640]
                    if dialogue:
                        await dialogue.dispatch({'action':'audio','turn_id':turn_id,
                            'pcm':base64.b64encode(chunk).decode('ascii')})
                    else:
                        if not self.send_audio(chunk, 16000, 1, manual=True):
                            return False
                        await asyncio.sleep(len(chunk) / 32000)
                if dialogue:
                    await dialogue.dispatch({'action':'drain','turn_id':turn_id})
                    await dialogue.dispatch({'action':'finish','turn_id':turn_id})
                return True
            finally:
                self.manual_playback = False
                if dialogue and dialogue.gate.turn_id == turn_id and dialogue.gate.busy and not dialogue.gate.stopping:
                    await dialogue.dispatch({'action':'failed','turn_id':turn_id})

    def clear(self):
        self.playback_generation += 1
        while not self.output.empty():
            self.output.get_nowait()
        self.output.put_nowait({'type': 'clear'})


class BrowserAudioBridge:
    def __init__(self, *, store, binding_for, stop_meeting):
        self.store, self.binding_for, self.stop_meeting = store, binding_for, stop_meeting
        self.leases = {}
        self.switching = set()

    def active(self, device_id):
        return self.leases.get(device_id)

    async def _save_before_switch(self, device_id):
        def latest():
            with self.store.lock:
                sid = self.store.active
                if not sid and self.store.rows:
                    sid = max(self.store.rows.values(), key=lambda r: r['requestedAt'])['sessionId']
                return self.store.status(device_id, sid or '')
        row = await asyncio.to_thread(latest)
        if row and row.get('ownerEntry', 'huibao') != 'huibao':
            raise ValueError('recording_not_owned')
        if row and (row['recordingState'] in {'preparing', 'recording'}
                    or (row['recordingState'] == 'stopped' and row.get('savedBytes'))):
            result = await self.stop_meeting(device_id, '惠宝，停止录音', str(uuid.uuid4()))
            if (result.get('recordingState') != 'stopped'
                    or (result.get('saveState') != 'saved' and result.get('savedBytes', 1) != 0)):
                raise ValueError('meeting_save_incomplete')

    async def acquire(self, device_id):
        if device_id in self.leases or device_id in self.switching:
            raise ValueError('browser_audio_busy')
        binding = self.binding_for(device_id)
        if binding is None or not binding.device_session.is_ready or not binding.rtc_session.connected:
            raise ValueError('device_not_connected')
        self.switching.add(device_id)
        try:
            await self._save_before_switch(device_id)
            if self.binding_for(device_id) is not binding or not binding.device_session.is_ready:
                raise ValueError('device_not_connected')
            dialogue = getattr(binding, '_dialogue', None)
            if dialogue is not None and dialogue.gate.busy and not dialogue.output_owner:
                await dialogue.stop()
                if dialogue.gate.busy:
                    raise ValueError('playback_unconfirmed')
            await binding.cancel_remote_playback(reason='browser_audio_takeover')
            # Invalidate any combined physical TTS/PB bundle before muting new audio.
            if hasattr(binding.device_session, 'send_pb_wire'):
                await binding.device_session.send_pb_wire('{"type":"pb_cancel"}')
            if hasattr(binding, 'set_browser_audio'):
                await binding.set_browser_audio(True)
            else:
                binding._discard_old_connection_audio()
                binding.device_session.audio_output_suspended = True
            lease = BrowserAudioLease(self, device_id, binding)
            self.leases[device_id] = lease
            dialogue = getattr(binding, '_dialogue', None)
            if dialogue is not None:
                dialogue.session_id = str(uuid.uuid4())
                dialogue.notify(dialogue.gate.status())
            return lease
        finally:
            self.switching.discard(device_id)

    async def release(self, lease, *, intentional):
        device_id = lease.device_id
        if self.leases.get(device_id) is not lease:
            return
        self.switching.add(device_id)
        try:
            if intentional:
                await self._save_before_switch(device_id)
                dialogue = getattr(lease.binding, '_dialogue', None)
                if dialogue is not None and dialogue.gate.busy:
                    await dialogue.stop()
                    await dialogue.gate.wait()
            else:
                # A failed durable save keeps ownership until the store can be reconciled.
                await asyncio.to_thread(self.store.interrupt, device_id, 'browser_audio_disconnected')
                dialogue = getattr(lease.binding, '_dialogue', None)
                if dialogue is not None:
                    await dialogue.disconnect()
            async with lease.source_lock:
                if lease.closed:
                    return
                lease.clear()
                await lease.binding.cancel_remote_playback(reason='browser_audio_release')
                if hasattr(lease.binding, 'set_browser_audio'):
                    await lease.binding.set_browser_audio(False)
                else:
                    lease.binding._discard_old_connection_audio()
                    lease.binding.device_session.audio_output_suspended = False
                lease.closed = True
                self.leases.pop(device_id, None)
                dialogue = getattr(lease.binding, '_dialogue', None)
                if dialogue is not None:
                    dialogue.session_id = str(uuid.uuid4())

        finally:
            self.switching.discard(device_id)

_tickets = TicketBook()
_bridge = None


def browser_audio_bridge():
    global _bridge
    if _bridge is None:
        from deskbot_server.pipeline.meeting_recording import meeting_recordings
        from deskbot_server import rtc_runtime
        from deskbot_server.application.meeting_voice import send_meeting_control
        _bridge = BrowserAudioBridge(store=meeting_recordings(),
                                     binding_for=lambda dev: rtc_runtime._bindings.get(dev),
                                     stop_meeting=send_meeting_control)
    return _bridge


def browser_audio_owner(device_id):
    return _bridge.active(device_id) if _bridge is not None else None


def browser_audio_switching(device_id):
    return bool(_bridge is not None and device_id in _bridge.switching)
