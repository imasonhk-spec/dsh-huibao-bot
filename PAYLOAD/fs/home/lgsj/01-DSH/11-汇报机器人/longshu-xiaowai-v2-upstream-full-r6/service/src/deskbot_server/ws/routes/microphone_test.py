"""Device microphone capture; uses the existing serial session and decoder."""
import json

from deskbot_server.pipeline.device_microphone_test import microphone_captures
from deskbot_server.ws.routes import ROUTES_API_KEY


async def handle(ctx, req):
    if req.method not in {"GET", "POST"} or (req.path_only.endswith("/audio") and req.method != "GET"):
        return ctx.json_resp(405, {"ok": False, "error": "method_not_allowed"})
    try:
        payload = json.loads(req.request.body or b"{}") if req.method == "POST" else req.qargs
        if not isinstance(payload, dict):
            raise ValueError("invalid JSON")
        device_id = str(payload.get("device_id") or "").strip()
        if not device_id:
            raise ValueError("请先选择设备。")
        denied = req.device_route_error(device_id)
        if denied is not None:
            return denied
        manager = ctx.serial_manager_provider() if callable(ctx.serial_manager_provider) else None
        source = manager.session_for_device(device_id) if manager else None
        if req.method == "POST" and payload.get("action") == "start":
            if source is None or not source.is_ready:
                raise ValueError("设备未连接，请检查 USB。")
            capture = microphone_captures.start(device_id, source)
        else:
            capture = microphone_captures.get(device_id, str(payload.get("capture_id") or ""))
            if capture is None:
                raise ValueError("录音不存在或已过期，请重新录制。")
            if req.method == "POST":
                if payload.get("action") != "stop":
                    raise ValueError("invalid action")
                microphone_captures.finish(capture)
        status = microphone_captures.status(capture, source)
        if req.path_only.endswith("/audio"):
            data = microphone_captures.wav(capture)
            headers = ctx.cors_headers()
            headers["Content-Type"] = "audio/wav"
            headers["Content-Length"] = str(len(data))
            headers["Cache-Control"] = "no-store"
            return ctx.Response(200, "OK", headers, data)
        return ctx.json_resp(200, status)
    except (ValueError, UnicodeError) as exc:
        return ctx.json_resp(400, {"ok": False, "error": str(exc)})


ROUTES_API_KEY["/api/microphone_test"] = handle
ROUTES_API_KEY["/api/microphone_test/audio"] = handle
