"""Authenticated per-device switch for continuous browser camera preview."""
import json
import re

from deskbot_server.application.camera_preview import preview_enabled, set_preview_enabled
from deskbot_server.ws.routes import ROUTES_API_KEY


async def handle(ctx, req):
    if req.method not in {"GET", "POST"}:
        return ctx.json_resp(405, {"ok": False, "error": "method_not_allowed"})
    try:
        payload = json.loads(req.request.body or b"{}") if req.method == "POST" else req.qargs
        if not isinstance(payload, dict):
            raise ValueError("请求格式错误")
        dev = str(payload.get("device_id") or "").strip()
        if not re.fullmatch(r"deskbot_[0-9a-f]{12}", dev):
            raise ValueError("请先选择设备")
        denied = req.device_route_error(dev)
        if denied is not None:
            return denied
        if req.method == "POST":
            if not isinstance(payload.get("enabled"), bool):
                raise ValueError("enabled 必须为布尔值")
            await set_preview_enabled(dev, payload["enabled"])
        return ctx.json_resp(200, {"ok": True, "device_id": dev, "enabled": preview_enabled(dev)})
    except (ValueError, UnicodeError) as exc:
        return ctx.json_resp(400, {"ok": False, "error": str(exc)})


ROUTES_API_KEY["/api/camera_preview"] = handle
