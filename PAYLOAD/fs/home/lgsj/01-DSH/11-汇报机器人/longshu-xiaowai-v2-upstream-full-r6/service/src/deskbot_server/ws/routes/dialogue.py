"""Device-scoped dialogue observation and explicit Stop behind the API-key gate."""
import re
from urllib.parse import unquote


def matches(path):
    return re.fullmatch(r'/api/devices/[^/]+/dialogue(?:/stop)?', path) is not None


async def handle(ctx, req):
    if not ctx.connection_is_loopback(req.connection):
        return ctx.json_resp(403, {'error':'loopback_required'})
    stopping = req.path_only.endswith('/stop')
    if req.method != ('POST' if stopping else 'GET'):
        return ctx.json_resp(405, {'error':'method_not_allowed'})
    device = unquote(req.path_only.split('/')[3])
    denied = req.device_route_error(device)
    if denied is not None:
        return denied
    from deskbot_server.application.meeting_voice import binding_for
    from deskbot_server import rtc_runtime
    from deskbot_server.application.core_dialogue import dialogue_for
    if binding_for(device) is None:
        return ctx.json_resp(403, {'error':'meeting_device_not_bound'})
    binding = rtc_runtime._bindings.get(device)
    if binding is None or not binding.device_session.is_ready:
        return ctx.json_resp(409, {'error':'device_not_connected'})
    controller = dialogue_for(binding)
    if stopping:
        await controller.stop()
    return ctx.json_resp(200, {'ok':True, 'device_id':device, 'dialogue':controller.status()})
