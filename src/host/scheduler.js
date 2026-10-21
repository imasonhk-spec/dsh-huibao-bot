/**
 * 会前提醒调度：每 30 秒看一次会议表，命中「开会前 N 分钟」就播报一次。
 * 去重键 = 会议 id + 开始时间，reminderLog 里存 7 天。
 */
import { loadConfig, loadState, updateState } from './config.js';
import { readMeetings } from './sources.js';
import { buildDigest } from './digest.js';
import { collect } from './sources.js';
import { speak } from './xiaowai.js';

const TICK_MS = 30000;

export function inQuietHours(cfg, now = new Date()) {
  const r = cfg?.reminder || {};
  const toMin = (s, dflt) => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(String(s || ''));
    return m ? Number(m[1]) * 60 + Number(m[2]) : dflt;
  };
  const qs = toMin(r.quietStart, 22 * 60);
  const qe = toMin(r.quietEnd, 8 * 60);
  const cur = now.getHours() * 60 + now.getMinutes();
  if (qs === qe) return false;
  if (qs < qe) return cur >= qs && cur < qe;
  return cur >= qs || cur < qe;
}

function recentlySpoken(state, windowMs) {
  const since = Date.now() - windowMs;
  return (state.speakLog || []).filter((x) => Number(x.at) >= since).length;
}

export function pruneLogs(state, now = Date.now()) {
  const week = now - 7 * 86400000;
  state.reminderLog = (state.reminderLog || []).filter((x) => Number(x.at) >= week);
  state.speakLog = (state.speakLog || []).filter((x) => Number(x.at) >= week);
  return state;
}

export async function runReminderTick(log = () => {}) {
  const cfg = loadConfig();
  if (!cfg?.reminder?.enabled) return { fired: 0, skipped: 'disabled' };
  const now = new Date();
  if (inQuietHours(cfg, now)) return { fired: 0, skipped: 'quiet_hours' };

  const meetings = readMeetings(now);
  if (!meetings.ok || !meetings.today.length) return { fired: 0, skipped: 'no_meetings' };

  const leadMs = Math.max(1, Number(cfg.reminder.minutesBefore || 10)) * 60000;
  const state = loadState();
  const seen = new Set((state.reminderLog || []).map((x) => `${x.id}@${x.startMs}`));
  let fired = 0;

  for (const m of meetings.today) {
    const delta = m.startMs - now.getTime();
    if (delta <= 0 || delta > leadMs) continue;
    const key = `${m.id}@${m.startMs}`;
    if (seen.has(key)) continue;
    if (recentlySpoken(state, 3600000) >= Number(cfg.reminder.maxPerHour || 4)) {
      log('[huibao] 每小时提醒上限已到，跳过：' + m.title);
      break;
    }

    let speakText = '';
    try {
      const digest = await collect(now);
      const built = await buildDigest(digest, { mode: 'reminder', focus: `「${m.title}」还有 ${Math.max(1, Math.round(delta / 60000))} 分钟开始` });
      speakText = built.speakText;
    } catch (error) {
      speakText = `${m.title}还有 ${Math.max(1, Math.round(delta / 60000))} 分钟开始。`;
      log('[huibao] 提醒稿生成失败，用兜底句：' + String(error?.message || error));
    }

    let result = { ok: false, error: 'speak_disabled' };
    if (cfg.reminder.speakOnReminder !== false) result = await speak(speakText, { scene: 'reminder' });

    updateState((s) => {
      s.reminderLog.push({ id: m.id, title: m.title, startMs: m.startMs, at: Date.now(), spoke: Boolean(result.ok), error: result.error || '' });
      if (result.ok) s.speakLog.push({ at: Date.now(), kind: 'reminder', text: speakText.slice(0, 500) });
      return pruneLogs(s);
    });
    seen.add(key);
    fired += 1;
    log(`[huibao] 会前提醒已触发：${m.title}（${result.ok ? '已播报' : '未播报:' + result.error}）`);
  }
  return { fired };
}

let timer = null;

export function startScheduler(log = () => {}) {
  if (timer) return () => {};
  const tick = () => {
    runReminderTick(log).catch((error) => log('[huibao] tick 异常：' + String(error?.message || error)));
  };
  tick();
  timer = setInterval(tick, TICK_MS);
  if (typeof timer.unref === 'function') timer.unref();
  return () => {
    clearInterval(timer);
    timer = null;
  };
}

export function stopScheduler() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}
