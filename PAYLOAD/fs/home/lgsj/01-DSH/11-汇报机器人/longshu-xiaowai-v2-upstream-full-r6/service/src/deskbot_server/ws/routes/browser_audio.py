"""Issue a short-lived single-use ticket behind the existing device API gate."""
import json

from deskbot_server.application.browser_audio import _tickets
from deskbot_server.ws.routes import ROUTES_API_KEY


async def handle(ctx, req):
    if req.method != 'POST':
        return ctx.json_resp(405, {'error':'method_not_allowed'})
    try:
        if not ctx.connection_is_loopback(req.connection):
            raise PermissionError('loopback_required')
        if len(req.request.body or b'') > 1024:
            raise ValueError('request_too_large')
        data = json.loads(req.request.body or b'{}')
        if not isinstance(data, dict):
            raise ValueError('invalid_request')
        device = str(data.get('device_id') or '')
        denied = req.device_route_error(device)
        if denied is not None:
            return denied
        from deskbot_server.application.meeting_voice import binding_for
        from deskbot_server.rtc_runtime import rtc_device_active
        if not binding_for(device):
            raise PermissionError('meeting_device_not_bound')
        if not rtc_device_active(device):
            return ctx.json_resp(409, {'error':'device_not_connected'})
        return ctx.json_resp(200, {'ticket':_tickets.issue(device), 'device_id':device})
    except PermissionError as exc:
        return ctx.json_resp(403, {'error':str(exc)})
    except (ValueError, TypeError) as exc:
        return ctx.json_resp(400, {'error':str(exc)})


ROUTES_API_KEY['/api/browser_audio_ticket'] = handle
