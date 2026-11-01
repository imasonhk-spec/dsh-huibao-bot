import asyncio
import json
from types import SimpleNamespace

from deskbot_server.ws.routes import ROUTES_API_KEY
from deskbot_server.ws.routes import camera_preview as route


def test_preview_control_checks_device_access_before_writing(monkeypatch):
    calls = []

    async def set_enabled(dev, enabled):
        calls.append((dev, enabled))

    monkeypatch.setattr(route, "set_preview_enabled", set_enabled)
    monkeypatch.setattr(route, "preview_enabled", lambda dev: False)
    ctx = SimpleNamespace(json_resp=lambda status, body: (status, body))
    req = SimpleNamespace(method="POST", qargs={},
        request=SimpleNamespace(body=json.dumps({"device_id":"deskbot_1cdbd46017ac", "enabled":False}).encode()),
        device_route_error=lambda dev: (403, {"ok":False}))
    assert ROUTES_API_KEY["/api/camera_preview"] is route.handle
    assert asyncio.run(route.handle(ctx, req))[0] == 403
    assert calls == []
    req.device_route_error = lambda dev: None
    status, body = asyncio.run(route.handle(ctx, req))
    assert status == 200 and body["enabled"] is False
    assert calls == [("deskbot_1cdbd46017ac", False)]
    req.request.body = b'{"device_id":"deskbot_1cdbd46017ac","enabled":"false"}'
    assert asyncio.run(route.handle(ctx, req))[0] == 400
    assert len(calls) == 1
