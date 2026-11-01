"""同源嵌入支持：DSH 工作台经 nginx `/xw/` 前缀反向代理本控制台时使用。

背景
----
DSH 页面运行在 ``https://<host>:3082/``（TLS），控制台原本只有
``http://<ip>:5050``。要把控制台嵌进 DSH，必须让两边**同源**：

* 否则 https 页面里的 http iframe 会被浏览器按「混合内容」直接拦掉；
* 而且控制台默认 ``X-Frame-Options: DENY`` / ``frame-ancestors 'none'``
  同样会拒绝被任何页面嵌入。

于是由 nginx 提供 ``location ^~ /xw/`` 反代到本服务，并带上
``X-Forwarded-Prefix: /xw``。本模块只在**该请求头存在时**才动手，
直接访问 5050 的行为保持完全不变：

1. 把响应体里的根绝对路径（``"/api/...``、``"/static/...``、``"/home"`` …）
   重写成带前缀的 ``/xw/...``，浏览器才会一直留在同源的 /xw/ 下；
2. 同步重写 ``Location`` 响应头（flask redirect）；
3. 提供「转发协议 / 主机 / 前缀」感知的 WebSocket origin（见 helpers）。

安全边界
--------
* 只接受形如 ``/[A-Za-z0-9_][A-Za-z0-9_/-]{0,63}`` 的前缀，防止头注入；
* 只改写**已知控制台路由首段**的路径，避免把 ``'/activate'`` 这类
  仅用于字符串拼接的片段误伤成 ``/xw/activate``。
"""

from __future__ import annotations

import re

#: 需要做前缀重写的文本响应类型；二进制/流式响应一律不动。
_TEXTUAL_TYPES = (
    "text/html",
    "text/css",
    "application/javascript",
    "text/javascript",
    "application/x-javascript",
)

#: 控制台自身路由的首段（来自 app.url_map，见 2026-10-21 的 /xw 改造）。
#: 只有命中这里的路径才会被加前缀：既覆盖页面/静态/API，又避免误改
#: 代码里 `'/activate'`、`'/export'` 之类的拼接片段。
_ROUTE_SEGMENTS = frozenset(
    {
        "advanced",
        "agent",
        "api",
        "app",
        "debug",
        "devices",
        "expr",
        "health",
        "home",
        "lab",
        "memories",
        "miot",
        "onboarding",
        "params",
        "playbooks",
        "proxy",
        "quest",
        "sessions",
        "static",
        "voice",
        "voice_gateway",
        "wifi",
    }
)

#: 引号包裹的根绝对路径，例如 `"/api/devices"` / `'/home'`。
_QUOTED_ROOT = re.compile(
    r"""(["'])/(?P<seg>[A-Za-z0-9_][A-Za-z0-9_.-]*)(?=[/?"'#]|$)"""
)

#: CSS / 内联样式里的 `url(/static/...)`（无引号）。
_UNQUOTED_URL = re.compile(
    r"""url\(/(?P<seg>[A-Za-z0-9_][A-Za-z0-9_.-]*)(?=[/?#)])"""
)

#: 合法前缀：/xw、/embed/console 之类，不允许出现空白、引号、反斜杠。
_PREFIX_RE = re.compile(r"^/[A-Za-z0-9_][A-Za-z0-9_/-]{0,63}$")


def embed_prefix(headers) -> str:
    """从请求头取出合法的嵌入前缀；不合法或缺失时返回空串。"""

    raw = (headers.get("X-Forwarded-Prefix") or "").strip()
    if raw.endswith("/"):
        raw = raw.rstrip("/")
    if not raw or not _PREFIX_RE.match(raw):
        return ""
    return raw


def prefix_rewrite_text(text: str, prefix: str) -> str:
    """把文本里的控制台根绝对路径重写成 ``<prefix>/...``。"""

    def quoted(match: "re.Match[str]") -> str:
        if match.group("seg") not in _ROUTE_SEGMENTS:
            return match.group(0)
        return match.group(0)[0] + prefix + match.group(0)[1:]

    def unquoted(match: "re.Match[str]") -> str:
        if match.group("seg") not in _ROUTE_SEGMENTS:
            return match.group(0)
        return "url(" + prefix + "/" + match.group("seg")

    text = _QUOTED_ROOT.sub(quoted, text)
    text = _UNQUOTED_URL.sub(unquoted, text)
    return text


def apply_prefix_rewrite(resp, prefix: str) -> None:
    """就地把响应体 / Location 改写成带前缀版本（失败时保持原样）。"""

    try:
        content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0]
        kind = content_type.strip().lower()
        if kind in _TEXTUAL_TYPES:
            # 静态 JS/CSS 由 send_file 提供，Flask 会把它当作流式响应/文件句柄
            # 透传；这里先读完再改写，否则前端的 fetch('/api/...') 依旧会跳到
            # origin 根路径（命中 DSH 的 /api 而不是控制台的 /api）。
            if resp.is_streamed or resp.direct_passthrough:
                chunks = []
                try:
                    for chunk in resp.response:
                        chunks.append(
                            chunk if isinstance(chunk, (bytes, bytearray)) else bytes(chunk)
                        )
                except TypeError:
                    chunks = []
                if chunks:
                    resp.set_data(b"".join(chunks))
            body = resp.get_data()
            if body:
                text = body.decode("utf-8", "replace")
                rewritten = prefix_rewrite_text(text, prefix)
                if rewritten != text:
                    resp.set_data(rewritten.encode("utf-8"))
                    resp.headers.pop("Content-Length", None)
                    resp.headers.pop("ETag", None)

        location = (resp.headers.get("Location") or "").strip()
        if (
            location.startswith("/")
            and not location.startswith("//")
            and location != prefix
            and not location.startswith(prefix + "/")
        ):
            resp.headers["Location"] = prefix + location
    except Exception:  # noqa: BLE001 - 嵌入改写绝不能让正常请求 500
        pass
