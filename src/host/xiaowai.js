/**
 * 小歪（Longshu xiaowai / deskbot-server）客户端
 *
 * 只走本机 loopback：host 与 deskbot-server 同机运行，`/api/*` 默认只允许 loopback。
 * 认证用服务自己生成的进程间凭证 data/.free_api_key（X-API-Key）。
 */
import { loadConfig, readLocalApiKey, readServiceEnv } from './config.js';

function baseUrl(cfg) {
  return String(cfg?.xiaowai?.baseUrl || 'http://127.0.0.1:9000').replace(/\/+$/, '');
}

function apiKey(cfg) {
  const cfgKey = cfg?.xiaowai?.apiKey;
  if (cfgKey) return cfgKey;
  return readLocalApiKey(cfg?.xiaowai?.serviceDir);
}

async function request(pathname, { method = 'GET', body, timeoutMs = 15000, needKey = true } = {}) {
  const cfg = loadConfig();
  const headers = { accept: 'application/json' };
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (needKey) {
    const key = apiKey(cfg);
    if (key) headers['x-api-key'] = key;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(baseUrl(cfg) + pathname, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }
    return { ok: res.ok, status: res.status, data };
  } catch (error) {
    return { ok: false, status: 0, error: error?.name === 'AbortError' ? 'timeout' : String(error?.message || error) };
  } finally {
    clearTimeout(timer);
  }
}

export async function health() {
  return request('/health', { needKey: false, timeoutMs: 6000 });
}

export async function listDevices() {
  const res = await request('/api/devices', { timeoutMs: 10000 });
  if (!res.ok) return { ...res, devices: [] };
  const raw = res.data;
  const list = Array.isArray(raw) ? raw : Array.isArray(raw?.devices) ? raw.devices : Array.isArray(raw?.items) ? raw.items : [];
  return {
    ...res,
    devices: list.map((d) => ({
      deviceId: d.device_id || d.deviceId || d.id || '',
      name: d.name || d.nickname || d.device_name || d.device_id || '',
      online: Boolean(d.online ?? d.connected ?? d.ws_connected ?? d.is_online),
      transport: d.transport || d.link || d.conn_type || '',
      firmware: d.firmware || d.fw_version || d.version || '',
      raw: d,
    })),
  };
}

/** 解析要播报的目标设备：配置优先，否则取第一个在线设备 */
export async function resolveDevice() {
  const cfg = loadConfig();
  const configured = cfg?.xiaowai?.deviceId || '';
  const listed = await listDevices();
  if (configured && (!listed.ok || listed.devices.length === 0)) {
    return { deviceId: configured, source: 'config', devices: [] };
  }
  const online = listed.devices.find((d) => d.online) || listed.devices[0];
  if (configured) {
    const hit = listed.devices.find((d) => d.deviceId === configured);
    if (hit) return { deviceId: hit.deviceId, source: 'config', devices: listed.devices, online: hit.online, transport: hit.transport };
  }
  return {
    deviceId: online?.deviceId || configured,
    source: online ? 'auto' : 'none',
    devices: listed.devices,
    online: online?.online,
    transport: online?.transport,
  };
}

/**
 * 让设备念一段话。
 * POST /api/device_tts { device_id, text, scene?, moves? } —— 走设备下行 TTS 通道。
 */
export async function speak(text, opts = {}) {
  const clean = String(text || '').trim();
  if (!clean) return { ok: false, error: 'empty_text' };
  const cfg = loadConfig();
  const target = opts.deviceId || (await resolveDevice()).deviceId;
  if (!target) {
    return { ok: false, error: 'no_device', message: '没有找到可用的小歪设备：请先插 USB 或配网，并在面板里选定设备' };
  }
  const payload = { device_id: target, text: clean };
  if (opts.scene) payload.scene = opts.scene;
  if (Array.isArray(opts.moves) && opts.moves.length) payload.moves = opts.moves;
  const res = await request('/api/device_tts', {
    method: 'POST',
    body: payload,
    timeoutMs: Number(cfg?.xiaowai?.speakTimeoutMs || 90000),
  });
  const data = res.data || {};
  const accepted = res.ok && data.ok !== false;
  return {
    ok: accepted,
    status: res.status,
    deviceId: target,
    operationId: data.operation_id || data.operation?.id || data.operationId || '',
    error: accepted ? '' : data.error || res.error || 'device_tts_failed',
    message: data.message || '',
    data,
  };
}

/** 把一句语音合成（不推设备），用于面板试听 */
export async function synthesize(text) {
  const cfg = loadConfig();
  const env = readServiceEnv(cfg?.xiaowai?.serviceDir);
  const endpoint = `${String(cfg?.speech?.ttsBaseUrl || '').replace(/\/+$/, '')}/audio/speech`;
  const key = cfg?.speech?.source === 'manual' && cfg?.speech?.apiKey ? cfg.speech.apiKey : env.TTS_API_KEY || cfg?.speech?.apiKey;
  if (!key) return { ok: false, error: 'no_tts_key' };
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
      body: JSON.stringify({
        model: cfg?.speech?.source === 'manual' && cfg?.speech?.ttsModel ? cfg.speech.ttsModel : env.TTS_MODEL || cfg?.speech?.ttsModel,
        voice: env.TTS_VOICE || cfg?.speech?.voice || 'serena',
        input: String(text || '').slice(0, 280),
        response_format: 'wav',
      }),
    });
    if (!res.ok) return { ok: false, status: res.status, error: 'tts_http_' + res.status };
    const buf = Buffer.from(await res.arrayBuffer());
    return { ok: true, audioBase64: buf.toString('base64'), bytes: buf.length };
  } catch (error) {
    return { ok: false, error: String(error?.message || error) };
  }
}

/** 服务整体快照：健康 + 设备 + RTC 状态（面板顶部用） */
export async function snapshot() {
  const [h, d] = await Promise.all([health(), listDevices()]);
  return {
    service: { ok: h.ok, status: h.status, data: h.data || null, error: h.error || '' },
    devices: d.devices || [],
    devicesError: d.ok ? '' : d.error || 'devices_unavailable',
  };
}
