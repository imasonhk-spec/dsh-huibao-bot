"""Continuous PCM recording, independent of dialogue, playback and captions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import struct
import threading
import time
import uuid

from deskbot_server.atomic_store import atomic_write_text


ACTIVE = {'preparing', 'recording'}
MAX_BYTES = 500 * 1024 * 1024 - 44  # Preserve the existing upload limit, including WAV header.


def wav_header(size, rate, channels):
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', size + 36, b'WAVE', b'fmt ',
                       16, 1, channels, rate, rate * channels * 2,
                       channels * 2, 16, b'data', size)


class MeetingRecordings:
    def __init__(self, root: Path, device_id: str):
        self.root = Path(root)
        self.device_id = device_id
        self.lock = threading.RLock()
        self.rows = {}
        self.handles = {}
        self.active = None
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for p in self.root.glob('*.json'):
            row = json.loads(p.read_text())
            self.rows[row['sessionId']] = row
            if row['recordingState'] in ACTIVE:
                audio = self.root / (row['sessionId'] + '.wav')
                if audio.exists():
                    row['savedBytes'] = max(0, audio.stat().st_size - 44)
                    with audio.open('r+b') as f:
                        f.write(wav_header(row['savedBytes'], row['sampleRate'], row['channels']))
                        f.flush()
                        os.fsync(f.fileno())
                self._finish(row, 'interrupted', 'service_restarted')

    def _allowed(self, device_id):
        if not self.device_id or device_id != self.device_id:
            raise PermissionError('meeting_device_not_bound')

    def _save(self, row):
        atomic_write_text(self.root / (row['sessionId'] + '.json'), json.dumps(row), mode=0o600)

    def _get(self, device_id, session_id=''):
        self._allowed(device_id)
        if session_id and not re.fullmatch(r'[a-f0-9]{32}', session_id):
            raise ValueError('invalid_session_id')
        row = self.rows.get(session_id or self.active or '')
        if row and row['deviceId'] != device_id:
            raise PermissionError('wrong_device')
        return row

    def status(self, device_id, session_id=''):
        with self.lock:
            row = self._get(device_id, session_id)
            if not row:
                return None
            result = dict(row)
            result['durationSeconds'] = row['savedBytes'] / (row['sampleRate'] * row['channels'] * 2)
            return result

    def start(self, device_id, request_id):
        with self.lock:
            self._allowed(device_id)
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
                raise ValueError('request_id_required')
            for row in self.rows.values():
                if row['requestId'] == request_id:
                    return self.status(device_id, row['sessionId'])
            if self.active:
                return self.status(device_id)
            sid = uuid.uuid4().hex
            row = dict(sessionId=sid, requestId=request_id, deviceId=device_id,
                       ownerEntry='huibao', sourceKey='device:'+device_id, recordingSessionId=sid,
                       recordingState='preparing', saveState='pending', savedBytes=0,
                       sampleRate=16000, channels=1, startedAt=None, endedAt=None,
                       requestedAt=time.time(), interruptionReason=None)
            self._save(row)
            self.rows[sid] = row
            self.active = sid
            return self.status(device_id, sid)

    def feed(self, device_id, pcm, sample_rate, channels):
        with self.lock:
            if device_id != self.device_id or not self.active or not pcm:
                return
            row = self.rows[self.active]
            if channels not in (1, 2) or not 8000 <= sample_rate <= 48000 or len(pcm) % (channels * 2):
                self._finish(row, 'interrupted', 'invalid_audio_format')
                return
            if row['savedBytes'] and (sample_rate != row['sampleRate'] or channels != row['channels']):
                self._finish(row, 'interrupted', 'audio_format_changed')
                return
            if row['savedBytes'] + len(pcm) > MAX_BYTES:
                self._finish(row, 'interrupted', 'recording_size_limit')
                return
            try:
                sid = row['sessionId']
                if sid not in self.handles:
                    row['sampleRate'], row['channels'] = sample_rate, channels
                    # Persist the format before the first audio bytes for crash recovery.
                    self._save(row)
                    fd = os.open(self.root / (sid + '.wav'), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                    f = os.fdopen(fd, 'r+b', buffering=0)
                    self.handles[sid] = f
                    f.write(wav_header(0, sample_rate, channels))
                f = self.handles[sid]
                f.seek(0, 2)
                written = f.write(pcm)
                if written != len(pcm):
                    raise OSError('short_audio_write')
                row['savedBytes'] += written
                f.seek(0)
                f.write(wav_header(row['savedBytes'], sample_rate, channels))
                # First-frame acknowledgement only follows a durable audio write.
                first = row['recordingState'] == 'preparing'
                if first or row['savedBytes'] // (sample_rate * channels * 2) != (row['savedBytes'] - written) // (sample_rate * channels * 2):
                    os.fsync(f.fileno())
                    if first:
                        row['startedAt'] = time.time() - written / (sample_rate * channels * 2)
                        row['recordingState'] = 'recording'
                    self._save(row)
            except OSError:
                self._finish(row, 'interrupted', 'audio_write_failed')

    def _finish(self, row, state, reason=None):
        f = self.handles.pop(row['sessionId'], None)
        if f:
            try:
                f.flush()
                os.fsync(f.fileno())
            finally:
                f.close()
        row['recordingState'] = state
        row['endedAt'] = time.time()
        row['interruptionReason'] = reason
        self._save(row)
        if self.active == row['sessionId']:
            self.active = None

    def stop(self, device_id, session_id):
        with self.lock:
            row = self._get(device_id, session_id)
            if row is None:
                raise ValueError('session_not_found')
            if row['recordingState'] in ACTIVE:
                self._finish(row, 'stopped')
            return self.status(device_id, session_id)

    def interrupt(self, device_id, reason):
        with self.lock:
            if device_id == self.device_id and self.active:
                self._finish(self.rows[self.active], 'interrupted', reason)

    def audio_path(self, device_id, session_id):
        with self.lock:
            row = self._get(device_id, session_id)
            if not row or row['recordingState'] in ACTIVE:
                raise ValueError('audio_not_sealed')
            if not row['savedBytes']:
                raise ValueError('empty_audio')
            return self.root / (session_id + '.wav')


_recordings = None


def note_audio_loss(device_id, reason):
    if _recordings is not None:
        _recordings.interrupt(device_id, reason)


def meeting_recordings():
    global _recordings
    if _recordings is None:
        from deskbot_server.paths import DATA_DIR
        # Installed explicitly by the validation owner; absent binding disables meeting control.
        config = DATA_DIR / 'local' / 'meeting-control.json'
        binding = json.loads(config.read_text()) if config.exists() else {}
        if binding and binding.get('account') != 'admin':
            raise ValueError('meeting_account_not_supported')
        _recordings = MeetingRecordings(DATA_DIR / 'local' / 'meeting-recordings', binding.get('deviceId', ''))
    return _recordings
