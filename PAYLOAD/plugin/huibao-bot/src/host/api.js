/**
 * 宿主 HTTP API：/api/huibao-bot/*
 * 认证：与工作台同一门禁（connection.requestRejection），由 index.js 注入。
 */
import { loadConfig, saveConfig, loadState, updateState } from './config.js';
import { collect } from './sources.js';
import { buildDigest } from './digest.js';
import { speak, snapshot } from './xiaowai.js';
import { chat, chatHistory, clearChat } from './chat.js';

function send(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  res.end(body);
}

function redact(cfg) {
  const mask = (v) => (v ? String(v).slice(0, 6) + '****' + String(v).slice(-4) : '');
  const out = JSON.parse(JSON.stringify(cfg));
  if (out.speech?.apiKey) out.speech.apiKey = mask(out.speech.apiKey);
  if (out.digestModel?.apiKey) out.digestModel.apiKey = mask(out.digestModel.apiKey);
  if (out.xiaowai?.apiKey) out.xiaowai.apiKey = mask(out.xiaowai.apiKey);
  return out;
}

function readBody(req, limit = 2 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) {
        reject(new Error('body_too_large'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw.trim()) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error('invalid_json'));
      }
    });
    req.on('error', reject);
  });
}

function asDue(input) {
  const s = String(input || '').trim();
  if (!s) return '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return `${s}T23:59`;
  return s.replace(' ', 'T');
}

export function createApi({ log = () => {} } = {}) {
  async function handle(req, res, pathname, method) {
    const m = String(method || req.method || 'GET').toUpperCase();
    const route = pathname.replace(/^\/api\/huibao-bot/, '') || '/';
    const url = new URL(req.url || '/', 'http://localhost');

    try {
      if (route === '/' || route === '/health') {
        const snap = await snapshot();
        return send(res, 200, {
          ok: true,
          id: 'huibao-bot',
          version: '0.1.0',
          service: snap.service,
          devices: snap.devices,
          devicesError: snap.devicesError,
        });
      }

      if (route === '/status' && m === 'GET') {
        const [snap, digest] = await Promise.all([snapshot(), collect()]);
        return send(res, 200, {
          ok: true,
          service: snap.service,
          devices: snap.devices,
          devicesError: snap.devicesError,
          counts: digest.counts,
          health: digest.health,
          config: redact(loadConfig()),
          reminderLog: (loadState().reminderLog || []).slice(-8).reverse(),
        });
      }

      if (route === '/devices' && m === 'GET') {
        const snap = await snapshot();
        return send(res, 200, { ok: snap.service.ok, devices: snap.devices, service: snap.service, error: snap.devicesError });
      }

      if (route === '/digest' && m === 'GET') {
        const mode = url.searchParams.get('mode') || 'full';
        const digest = await collect();
        const built = await buildDigest(digest, { mode });
        return send(res, 200, { ok: true, ...built, counts: digest.counts, health: digest.health, raw: digest });
      }

      if (route === '/speak' && m === 'POST') {
        const body = await readBody(req);
        let text = String(body.text || '').trim();
        let source = 'explicit';
        let built = null;
        if (!text) {
          const digest = await collect();
          built = await buildDigest(digest, { mode: body.mode || 'full' });
          text = built.speakText;
          source = 'digest';
        }
        const result = await speak(text, { deviceId: body.deviceId, scene: body.scene });
        updateState((s) => {
          if (result.ok) s.speakLog.push({ at: Date.now(), kind: source, text: text.slice(0, 500) });
          if (s.speakLog.length > 200) s.speakLog = s.speakLog.slice(-200);
          return s;
        });
        log(`[huibao] speak ${result.ok ? 'ok' : 'fail'} ${result.deviceId || ''} ${result.error || ''}`);
        return send(res, result.ok ? 200 : 502, { ok: result.ok, result, text, source, headline: built?.headline || '' });
      }

      if (route === '/chat' && m === 'POST') {
        const body = await readBody(req);
        const result = await chat(body.text, { speak: Boolean(body.speak) });
        return send(res, result.ok ? 200 : 400, result);
      }
      if (route === '/chat' && m === 'GET') return send(res, 200, { ok: true, items: chatHistory(40) });
      if (route === '/chat' && m === 'DELETE') return send(res, 200, clearChat());

      if (route === '/config' && m === 'GET') return send(res, 200, { ok: true, config: redact(loadConfig()) });
      if (route === '/config' && m === 'PUT') {
        const body = await readBody(req);
        const patch = body.config || body.patch || body || {};
        // 打码值不回写，避免把 "abcd****wxyz" 存成真 Key
        const strip = (obj) => {
          for (const k of Object.keys(obj || {})) {
            if (/apiKey$/i.test(k) && typeof obj[k] === 'string' && obj[k].includes('****')) delete obj[k];
            else if (obj[k] && typeof obj[k] === 'object') strip(obj[k]);
          }
        };
        strip(patch);
        const next = saveConfig(patch);
        log('[huibao] 配置已更新');
        return send(res, 200, { ok: true, config: redact(next) });
      }

      if (route === '/todos' && m === 'GET') {
        const s = loadState();
        return send(res, 200, { ok: true, items: s.todos || [] });
      }
      if (route === '/todos' && m === 'POST') {
        const body = await readBody(req);
        const text = String(body.text || '').trim();
        if (!text) return send(res, 400, { ok: false, error: 'missing_text' });
        const item = {
          id: 'todo-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
          text,
          due: asDue(body.due),
          priority: body.priority || '',
          done: false,
          createdAt: Date.now(),
        };
        updateState((s) => {
          s.todos.push(item);
          return s;
        });
        return send(res, 200, { ok: true, item });
      }
      if (route.startsWith('/todos/')) {
        const id = decodeURIComponent(route.slice('/todos/'.length));
        const s = loadState();
        const item = (s.todos || []).find((t) => t.id === id);
        if (!item) return send(res, 404, { ok: false, error: 'not_found' });
        if (m === 'PATCH' || m === 'PUT') {
          const body = await readBody(req);
          updateState((st) => {
            const hit = st.todos.find((t) => t.id === id);
            if (typeof body.text === 'string') hit.text = body.text;
            if (body.due !== undefined) hit.due = asDue(body.due);
            if (body.priority !== undefined) hit.priority = body.priority;
            if (body.done !== undefined) {
              hit.done = Boolean(body.done);
              hit.doneAt = hit.done ? Date.now() : 0;
            }
            return st;
          });
          return send(res, 200, { ok: true, item });
        }
        if (m === 'DELETE') {
          updateState((st) => {
            st.todos = st.todos.filter((t) => t.id !== id);
            return st;
          });
          return send(res, 200, { ok: true });
        }
      }

      if (route === '/reminder/test' && m === 'POST') {
        const body = await readBody(req);
        const digest = await collect();
        const built = await buildDigest(digest, { mode: 'reminder', focus: body.focus || '测试提醒' });
        const result = body.dryRun ? { ok: true, dryRun: true } : await speak(built.speakText, { scene: 'reminder_test' });
        return send(res, 200, { ok: true, text: built.speakText, result });
      }

      return send(res, 404, { ok: false, error: 'not_found', route });
    } catch (error) {
      log('[huibao] API 异常：' + String(error?.stack || error));
      return send(res, 500, { ok: false, error: String(error?.message || error) });
    }
  }

  return { handle };
}
