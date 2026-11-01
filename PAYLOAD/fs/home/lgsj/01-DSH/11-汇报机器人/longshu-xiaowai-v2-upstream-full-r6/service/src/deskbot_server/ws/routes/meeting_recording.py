"""Bound admin meeting audio; existing API-key gate remains authoritative."""
import asyncio
import json

from deskbot_server.pipeline.meeting_recording import meeting_recordings
from deskbot_server.ws.routes import ROUTES_API_KEY


async def handle(ctx, req):
    try:
        if not ctx.connection_is_loopback(req.connection):
            raise PermissionError('loopback_required')
        if req.method not in {'GET', 'POST'}:
            return ctx.json_resp(405, {'error': 'method_not_allowed'})
        if len(req.request.body or b'') > 4096:
            raise ValueError('request_too_large')
        data = json.loads(req.request.body or b'{}') if req.method == 'POST' else req.qargs
        if not isinstance(data, dict):
            raise ValueError('invalid_request')
        device = str(data.get('device_id') or '')
        denied = req.device_route_error(device)
        if denied is not None:
            return denied
        store = meeting_recordings()
        store._allowed(device)
        sid = str(data.get('session_id') or '')
        if req.path_only.endswith(('/audio', '/pcm')):
            if req.method != 'GET':
                return ctx.json_resp(405, {'error': 'method_not_allowed'})
            offset, length = int(data.get('offset', 0)), int(data.get('length', 1024 * 1024))
            if offset < 0 or not 1 <= length <= 1024 * 1024:
                raise ValueError('invalid_audio_range')
            if req.path_only.endswith('/pcm'):
                row = store.status(device, sid)
                if not row or not row['savedBytes']:
                    raise ValueError('audio_not_ready')
                file = store.root / (sid + '.wav')
                offset += 44
                length = min(length, max(0, row['savedBytes'] - (offset - 44)))
            else:
                file = store.audio_path(device, sid)
            def read():
                with file.open('rb') as f:
                    f.seek(offset)
                    return f.read(length)
            audio = await asyncio.to_thread(read)
            headers = ctx.cors_headers()
            headers.update({'Content-Type': 'application/octet-stream', 'Content-Length': str(len(audio)), 'Cache-Control': 'no-store'})
            return ctx.Response(200, 'OK', headers, audio)
        if req.method == 'POST':
            if data.get('action') == 'start':
                manager = ctx.serial_manager_provider() if callable(ctx.serial_manager_provider) else None
                source = manager.session_for_device(device) if manager else None
                if source is None or not source.is_ready:
                    return ctx.json_resp(409, {'error': 'device_not_connected'})
                def start():
                    from deskbot_server.application.browser_audio import browser_audio_switching
                    if browser_audio_switching(device):
                        raise ValueError('audio_source_switch_in_progress')
                    return store.start(device, data.get('request_id'))
                row = await asyncio.to_thread(start)
            elif data.get('action') == 'stop':
                row = await asyncio.to_thread(store.stop, device, sid)
            else:
                raise ValueError('invalid_action')
        else:
            if not sid and store.rows:
                sid = store.active or max(store.rows.values(), key=lambda row: row['requestedAt'])['sessionId']
            row = store.status(device, sid)
        return ctx.json_resp(200, row or {'recordingState': 'idle'})
    except PermissionError as e:
        return ctx.json_resp(403, {'error': str(e)})
    except (ValueError, TypeError, UnicodeError) as e:
        return ctx.json_resp(400, {'error': str(e)})
    except OSError:
        return ctx.json_resp(503, {'error': 'meeting_storage_unavailable'})


ROUTES_API_KEY['/api/meeting_recording'] = handle
ROUTES_API_KEY['/api/meeting_recording/audio'] = handle
ROUTES_API_KEY['/api/meeting_recording/pcm'] = handle
