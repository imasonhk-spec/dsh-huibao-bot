/**
 * 智汇 sidecar 静态 + 反代服务（零第三方依赖）
 *
 * 环境变量：
 *   PORT          监听端口
 *   BASE_PREFIX   同源挂载前缀，如 /zhihui/project（静态资源前缀）
 *   WEB_ROOT      前端构建产物目录
 *   PROXY_RULES   JSON 数组：[{ "match": "/zhihui/project/api", "target": "http://127.0.0.1:3100", "replace": "/api" }]
 *
 * 行为：
 *   - 命中 PROXY_RULES → 流式反代（支持 SSE / 上传，不缓冲）
 *   - {BASE_PREFIX}/health → 本服务健康信息
 *   - 其余 → 去掉 BASE_PREFIX 后从 WEB_ROOT 取文件；找不到且无扩展名时回退 index.html（SPA）
 */
import http from 'node:http'
import { createReadStream, readFileSync } from 'node:fs'
import { stat } from 'node:fs/promises'
import path from 'node:path'

const PORT = Number(process.env.PORT || 3200)
const BASE = (process.env.BASE_PREFIX || '').replace(/\/$/, '')
const ROOT = path.resolve(process.env.WEB_ROOT || '.')
const RULES = (() => {
  if (process.env.PROXY_RULES_FILE) {
    const raw = readFileSync(process.env.PROXY_RULES_FILE, 'utf8')
    return JSON.parse(raw)
  }
  return JSON.parse(process.env.PROXY_RULES || '[]')
})()

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.map': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.pdf': 'application/pdf',
  '.csv': 'text/csv; charset=utf-8',
}

function sendJson(res, status, body) {
  const payload = JSON.stringify(body)
  try {
    res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
    res.end(payload)
  } catch {
    /* ignore */
  }
}

function matchRule(pathname) {
  for (const rule of RULES) {
    if (pathname === rule.match || pathname.startsWith(rule.match + '/') || pathname.startsWith(rule.match + '?')) {
      return rule
    }
  }
  return null
}

function proxy(req, res, rule, pathname, search) {
  let target
  try {
    target = new URL(rule.target)
  } catch {
    sendJson(res, 500, { ok: false, error: 'BAD_TARGET', target: rule.target })
    return
  }
  const rewritten = pathname.replace(rule.match, rule.replace || '')
  const upstreamPath = (rewritten.startsWith('/') ? rewritten : '/' + rewritten) + (search || '')
  const headers = { ...req.headers }
  headers.host = target.host
  headers['x-forwarded-host'] = req.headers.host || ''
  headers['x-forwarded-proto'] = req.headers['x-forwarded-proto'] || 'http'
  const xff = req.headers['x-forwarded-for']
  headers['x-forwarded-for'] = xff ? `${xff}, ${req.socket.remoteAddress}` : String(req.socket.remoteAddress || '')

  const upstream = http.request(
    {
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || (target.protocol === 'https:' ? 443 : 80),
      method: req.method,
      path: upstreamPath,
      headers,
    },
    (up) => {
      const outHeaders = { ...up.headers }
      // 上游压缩由浏览器协商，这里透传即可
      try {
        res.writeHead(up.statusCode || 502, outHeaders)
      } catch {
        up.destroy()
        return
      }
      up.pipe(res)
    },
  )

  upstream.setTimeout(600000, () => {
    upstream.destroy(new Error('upstream_timeout'))
  })
  upstream.on('error', (error) => {
    if (res.headersSent) {
      try {
        res.destroy()
      } catch {
        /* ignore */
      }
      return
    }
    sendJson(res, 502, { ok: false, error: 'UPSTREAM_UNAVAILABLE', rule: rule.match, detail: String(error && error.message) })
  })
  req.pipe(upstream)
}

function serveFile(req, res, filePath, statInfo) {
  const ext = path.extname(filePath).toLowerCase()
  const etag = `W/"${statInfo.size}-${Math.floor(statInfo.mtimeMs)}"`
  if (req.headers['if-none-match'] === etag) {
    res.writeHead(304, { etag })
    res.end()
    return
  }
  const isHtml = ext === '.html'
  res.writeHead(200, {
    'content-type': MIME[ext] || 'application/octet-stream',
    'content-length': statInfo.size,
    etag,
    'last-modified': statInfo.mtime.toUTCString(),
    'cache-control': isHtml ? 'no-store' : 'public, max-age=31536000, immutable',
  })
  if (req.method === 'HEAD') {
    res.end()
    return
  }
  const stream = createReadStream(filePath)
  stream.on('error', () => {
    try {
      res.destroy()
    } catch {
      /* ignore */
    }
  })
  stream.pipe(res)
}

async function handleStatic(req, res, pathname) {
  if (!pathname.startsWith(BASE + '/') || !BASE) {
    if (pathname === BASE) {
      res.writeHead(302, { location: BASE + '/' })
      res.end()
      return
    }
    sendJson(res, 404, { ok: false, error: 'OUT_OF_SCOPE', base: BASE })
    return
  }
  let rel = pathname.slice(BASE.length)
  try {
    rel = decodeURIComponent(rel)
  } catch {
    sendJson(res, 400, { ok: false, error: 'BAD_PATH' })
    return
  }
  const safeRel = path.normalize(rel).replace(/^(\.\.[/\\])+/, '')
  let filePath = path.join(ROOT, safeRel)
  if (!filePath.startsWith(ROOT)) {
    sendJson(res, 403, { ok: false, error: 'FORBIDDEN' })
    return
  }
  try {
    let info = await stat(filePath)
    if (info.isDirectory()) {
      filePath = path.join(filePath, 'index.html')
      info = await stat(filePath)
    }
    serveFile(req, res, filePath, info)
    return
  } catch {
    /* fallthrough to SPA */
  }
  // SPA 回退：无扩展名的前端路由一律回 index.html
  if (!path.extname(safeRel)) {
    const indexPath = path.join(ROOT, 'index.html')
    try {
      const info = await stat(indexPath)
      serveFile(req, res, indexPath, info)
      return
    } catch {
      /* ignore */
    }
  }
  sendJson(res, 404, { ok: false, error: 'NOT_FOUND', path: pathname })
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', 'http://127.0.0.1')
  const pathname = url.pathname

  if (pathname === BASE + '/health' || pathname === BASE + '/health/') {
    sendJson(res, 200, { ok: true, service: 'zhihui-sidecar', base: BASE, root: ROOT, port: PORT, rules: RULES.length })
    return
  }

  const rule = matchRule(pathname)
  if (rule) {
    proxy(req, res, rule, pathname, url.search)
    return
  }

  if (req.method !== 'GET' && req.method !== 'HEAD') {
    sendJson(res, 405, { ok: false, error: 'METHOD_NOT_ALLOWED' })
    return
  }

  handleStatic(req, res, pathname).catch((error) => {
    if (!res.headersSent) sendJson(res, 500, { ok: false, error: String((error && error.message) || error) })
  })
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[zhihui-sidecar] ${BASE} → http://127.0.0.1:${PORT} root=${ROOT} rules=${RULES.length}`)
})

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    server.close(() => process.exit(0))
  })
}
