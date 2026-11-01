/**
 * 智汇 SSO 换票 / 续票服务（DSH multi-user → 智汇 zh_session）
 *
 * 目的：用户已登录 DSH 时，打开「智汇·项目/商机」不再跳门户登录页，且会话能自动续期。
 *
 * 身份校验（fail-closed）：
 *   1. dsh_mu_user           —— 网关登录时下发的用户名（仅作名字，不作凭据）
 *   2. dsh_mu_session        —— 网关登录时下发的随机令牌，必须能在
 *                               <DSH_MU_SESSIONS_FILE> 中找到、未过期、且用户名一致
 *   两者缺一即 401。只认 dsh_mu_user 是不安全的：该 cookie 是明文用户名，任何人可伪造。
 *
 * 接口：
 *   GET /zhihui-sso/session?app=project|opportunity
 *       200 {ok:true, username, app, reused, expiresAt, remainingSeconds, idleSeconds, keepaliveSeconds}
 *           + Set-Cookie: zh_session=<uuid>（已存在有效会话则复用并滑动续期）
 *       401 {ok:false, error:'DSH_SESSION_*'} 不是合法的 DSH 登录态
 *       400 {ok:false, error:'BAD_APP'}
 *   GET /zhihui-sso/session?app=..&to=<url>     → 302 回 to（会话过期后自动续票）
 *   GET /zhihui-sso/session?app=..（浏览器导航）→ 302 回该应用根路径（避免落到裸 JSON）
 *   GET /zhihui-sso/health
 *
 * 续期策略：与智汇一致，闲置 2h（滑动）+ 绝对 24h。本服务每次被调用都会把
 * expires_at 推到 now+2h，因此面板侧按 keepaliveSeconds（默认 25 分钟）轻量轮询即可保活。
 *
 * 依赖：pg（复用 project-manager 的 node_modules），Postgres 里的 auth_users / auth_sessions。
 */
import http from 'node:http'
import { randomBytes } from 'node:crypto'
import { readFileSync, statSync } from 'node:fs'
import { createRequire } from 'node:module'

const PORT = Number(process.env.SSO_PORT || 3220)
const DATABASE_URL = process.env.DATABASE_URL
const SESSION_COOKIE = process.env.ZH_SESSION_COOKIE_NAME || 'zh_session'
const IDLE_TTL_SECONDS = Number(process.env.ZH_IDLE_TTL_SECONDS || 7200)
const ABS_TTL_HOURS = 24
const IDLE_TTL = `${IDLE_TTL_SECONDS} seconds`
/** 建议客户端续期间隔：闲置窗口的 1/6（2h → 20min），且不短于 60s。 */
const KEEPALIVE_SECONDS = Math.max(60, Math.floor(IDLE_TTL_SECONDS / 6))

const DSH_MU_SESSIONS_FILE = process.env.DSH_MU_SESSIONS_FILE || '/home/lgsj/.dsh/multi-user/sessions.json'
/** 逃生开关：仅用于「没有 multi-user 插件」的单用户部署，等于关掉校验，默认关闭。 */
const TRUST_USER_COOKIE = process.env.SSO_TRUST_USER_COOKIE === '1'

/** app → 该应用的根路径（续票回跳与导航兜底用）。 */
const APP_PATH = {
  project: '/zhihui/project/',
  opportunity: '/zhihui/opportunity/',
}
const APP_ALIAS = { project: 'project', opportunity: 'opportunity' }
const USERNAME_RE = /^[A-Za-z0-9_-]{1,64}$/
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f-]{27}$/i

if (!DATABASE_URL) {
  console.error('[zhihui-sso] DATABASE_URL 未设置')
  process.exit(1)
}

const requireFromProject = createRequire(process.env.PROJECT_HOME ? process.env.PROJECT_HOME + '/' : '/opt/zhihui/project-manager/')
const { Pool } = requireFromProject('pg')
const pool = new Pool({ connectionString: DATABASE_URL, max: 4 })

function sendJson(res, status, body) {
  const payload = JSON.stringify(body)
  try {
    res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
    res.end(payload)
  } catch {
    /* ignore */
  }
}

function redirect(res, location) {
  try {
    res.writeHead(302, { location, 'cache-control': 'no-store' })
    res.end()
  } catch {
    /* ignore */
  }
}

function parseCookies(header) {
  const out = {}
  for (const part of String(header || '').split(';')) {
    const index = part.indexOf('=')
    if (index <= 0) continue
    const key = part.slice(0, index).trim()
    const value = part.slice(index + 1).trim()
    if (key) {
      try {
        out[key] = decodeURIComponent(value)
      } catch {
        out[key] = value
      }
    }
  }
  return out
}

/* ---------- DSH 登录态校验 ---------- */

let sessionCache = { mtimeMs: -1, tokens: new Map() }

/** 按 mtime 缓存的 sessions.json 读取；token → {username, expiresAt, ...} */
function loadGatewaySessions() {
  try {
    const st = statSync(DSH_MU_SESSIONS_FILE)
    if (st.mtimeMs !== sessionCache.mtimeMs) {
      const raw = JSON.parse(readFileSync(DSH_MU_SESSIONS_FILE, 'utf8'))
      const list = Array.isArray(raw && raw.sessions) ? raw.sessions : []
      const tokens = new Map()
      for (const entry of list) {
        if (entry && typeof entry.token === 'string' && entry.token) tokens.set(entry.token, entry)
      }
      sessionCache = { mtimeMs: st.mtimeMs, tokens }
    }
    return { tokens: sessionCache.tokens, error: null }
  } catch (error) {
    return { tokens: new Map(), error: String((error && error.message) || error) }
  }
}

/**
 * 校验请求是否带有合法的 DSH 登录态。
 * @returns {{ok:true, username:string} | {ok:false, error:string, reason:string}}
 */
function verifyDshIdentity(cookies) {
  const user = String(cookies.dsh_mu_user || '').trim()
  if (!user || !USERNAME_RE.test(user)) {
    return { ok: false, error: 'DSH_USER_REQUIRED', reason: user ? 'dsh_mu_user 含非法字符' : '缺少 dsh_mu_user cookie（DSH 多用户登录态）' }
  }

  if (TRUST_USER_COOKIE) return { ok: true, username: user }

  const token = String(cookies.dsh_mu_session || '').trim()
  if (!token) {
    return { ok: false, error: 'DSH_SESSION_REQUIRED', reason: '缺少 dsh_mu_session（DSH 登录会话），请重新登录工作台' }
  }

  const { tokens, error } = loadGatewaySessions()
  if (error) {
    return { ok: false, error: 'DSH_SESSION_UNAVAILABLE', reason: `DSH 会话库不可读：${error}` }
  }

  const entry = tokens.get(token)
  if (!entry) {
    return { ok: false, error: 'DSH_SESSION_INVALID', reason: 'DSH 登录会话已失效，请重新登录工作台' }
  }
  if (Number(entry.expiresAt || 0) <= Date.now()) {
    return { ok: false, error: 'DSH_SESSION_EXPIRED', reason: 'DSH 登录会话已过期，请重新登录工作台' }
  }
  if (String(entry.username || '') !== user) {
    return { ok: false, error: 'DSH_SESSION_MISMATCH', reason: 'dsh_mu_user 与登录会话不一致' }
  }
  return { ok: true, username: user }
}

/* ---------- 智汇账号与会话 ---------- */

/** 与智汇 auth.ts 的 hashPassword 同格式（scrypt$salt$hash）；SSO 账号不使用密码登录，仅满足 NOT NULL。 */
function placeholderPasswordHash() {
  const salt = randomBytes(16)
  const hash = randomBytes(64)
  return `scrypt$${salt.toString('hex')}$${hash.toString('hex')}`
}

async function ensureUser(username) {
  const existing = await pool.query('SELECT id, username, display_name FROM auth_users WHERE username = $1', [username])
  if (existing.rows[0]) return { ...existing.rows[0], created: false }
  const inserted = await pool.query(
    `INSERT INTO auth_users (username, display_name, password_hash)
     VALUES ($1, $1, $2)
     ON CONFLICT (username) DO UPDATE SET display_name = COALESCE(auth_users.display_name, EXCLUDED.display_name)
     RETURNING id, username, display_name`,
    [username, placeholderPasswordHash()],
  )
  return { ...inserted.rows[0], created: true }
}

/**
 * 取一个可用会话：优先复用剩余寿命 >5min 且未超 24h 绝对上限的，并滑动续期到 now+2h。
 * @returns {{id:string, reused:boolean, expiresAt:string, remainingSeconds:number}}
 */
async function ensureSession(userId, app) {
  const reusable = await pool.query(
    `UPDATE auth_sessions
        SET last_seen_at = now(), expires_at = now() + $1::interval
      WHERE id = (
        SELECT id FROM auth_sessions
         WHERE user_id = $2 AND app = $3
           AND expires_at > now() + interval '5 minutes'
           AND created_at > now() - ($4 || ' hours')::interval
         ORDER BY created_at DESC LIMIT 1
      )
      RETURNING id, expires_at`,
    [IDLE_TTL, userId, app, String(ABS_TTL_HOURS)],
  )
  if (reusable.rows[0]) {
    const row = reusable.rows[0]
    return {
      id: row.id,
      reused: true,
      expiresAt: new Date(row.expires_at).toISOString(),
      remainingSeconds: Math.max(0, Math.round((new Date(row.expires_at).getTime() - Date.now()) / 1000)),
    }
  }
  const created = await pool.query(
    `INSERT INTO auth_sessions (user_id, app, expires_at) VALUES ($1, $2, now() + $3::interval)
     RETURNING id, expires_at`,
    [userId, app, IDLE_TTL],
  )
  const row = created.rows[0]
  return {
    id: row.id,
    reused: false,
    expiresAt: new Date(row.expires_at).toISOString(),
    remainingSeconds: Math.max(0, Math.round((new Date(row.expires_at).getTime() - Date.now()) / 1000)),
  }
}

function sessionCookie(sessionId) {
  return `${SESSION_COOKIE}=${sessionId}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${ABS_TTL_HOURS * 3600}`
}

/**
 * 解析回跳目标：
 *   - 没给 to → 该应用根路径
 *   - to 是纯 origin（智汇 auth-gate 就是这么传的）→ 补上应用根路径，避免落到工作台首页
 *   - to 是相对路径 → 直接当应用内路径用
 */
function resolveRedirect(to, app) {
  const appPath = APP_PATH[app] || '/'
  if (!to) return appPath
  if (to.startsWith('/')) return to
  let url
  try {
    url = new URL(to)
  } catch {
    return appPath
  }
  if (!/^https?:$/i.test(url.protocol)) return appPath
  if (url.pathname === '' || url.pathname === '/') url.pathname = appPath
  return url.toString()
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', 'http://127.0.0.1')
  const pathname = url.pathname.replace(/\/+$/, '') || '/'

  if (pathname === '/zhihui-sso/health') {
    try {
      await pool.query('SELECT 1')
      const { tokens, error } = loadGatewaySessions()
      sendJson(res, 200, {
        ok: true,
        service: 'zhihui-sso',
        db: 'up',
        gatewaySessions: error ? `unreadable: ${error}` : tokens.size,
        trustUserCookie: TRUST_USER_COOKIE,
        idleSeconds: IDLE_TTL_SECONDS,
        keepaliveSeconds: KEEPALIVE_SECONDS,
      })
    } catch (error) {
      sendJson(res, 503, { ok: false, service: 'zhihui-sso', db: 'down', detail: String(error && error.message) })
    }
    return
  }

  if (pathname !== '/zhihui-sso/session') {
    sendJson(res, 404, { ok: false, error: 'NOT_FOUND' })
    return
  }

  const appRaw = String(url.searchParams.get('app') || 'project').toLowerCase()
  const app = APP_ALIAS[appRaw]
  if (!app) {
    sendJson(res, 400, { ok: false, error: 'BAD_APP', app: appRaw })
    return
  }

  const cookies = parseCookies(req.headers.cookie)
  const identity = verifyDshIdentity(cookies)
  if (!identity.ok) {
    sendJson(res, 401, { ok: false, error: identity.error, reason: identity.reason })
    return
  }

  try {
    const user = await ensureUser(identity.username)
    const session = await ensureSession(user.id, app)
    res.setHeader('set-cookie', sessionCookie(session.id))

    const to = String(url.searchParams.get('to') || '')
    // 浏览器直接导航（Accept 含 text/html）或显式带 to → 续票后回跳应用，避免用户看到裸 JSON
    const wantsHtml = /\btext\/html\b/i.test(String(req.headers.accept || ''))
    if (to || wantsHtml) {
      redirect(res, resolveRedirect(to, app))
      return
    }

    sendJson(res, 200, {
      ok: true,
      username: user.username,
      displayName: user.display_name,
      app,
      reused: session.reused,
      created: user.created,
      sessionId: session.id.slice(0, 8) + '…',
      expiresAt: session.expiresAt,
      remainingSeconds: session.remainingSeconds,
      idleSeconds: IDLE_TTL_SECONDS,
      keepaliveSeconds: KEEPALIVE_SECONDS,
    })
  } catch (error) {
    console.error('[zhihui-sso]', error)
    sendJson(res, 500, { ok: false, error: 'SSO_FAILED', reason: String((error && error.message) || error) })
  }
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[zhihui-sso] listening on http://127.0.0.1:${PORT}`)
  console.log(`[zhihui-sso] idle=${IDLE_TTL_SECONDS}s abs=${ABS_TTL_HOURS}h keepalive=${KEEPALIVE_SECONDS}s`)
  console.log(`[zhihui-sso] gateway sessions: ${DSH_MU_SESSIONS_FILE} (trust-user-cookie=${TRUST_USER_COOKIE})`)
})

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    server.close(() => pool.end().finally(() => process.exit(0)))
  })
}

void UUID_RE
