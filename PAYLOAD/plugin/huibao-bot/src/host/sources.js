/**
 * 数据源聚合：会议日程 / Bot 群聊通知 / 工作台任务 / 系统告警 / 手动待办
 *
 * 设计原则：
 *  - 全部只读，且每个源独立容错（缺文件/结构变化只让该源为空，不影响整体）；
 *  - 结构做宽松归一（会议 JSON 字段名在不同版本里不一致）；
 *  - 项目/供应链进度先留扩展位（config.projects + sources.projects）。
 */
import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { dshHome, loadConfig, loadState } from './config.js';

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

function toMs(value) {
  if (value === undefined || value === null || value === '') return 0;
  if (typeof value === 'number') return value > 1e12 ? value : value * 1000;
  const s = String(value).trim();
  if (/^\d+$/.test(s)) {
    const n = Number(s);
    return n > 1e12 ? n : n * 1000;
  }
  const t = Date.parse(s.includes('T') ? s : s.replace(' ', 'T'));
  return Number.isNaN(t) ? 0 : t;
}

function pick(obj, names) {
  for (const n of names) {
    if (obj && obj[n] !== undefined && obj[n] !== null && obj[n] !== '') return obj[n];
  }
  return undefined;
}

export function dayRange(now = new Date()) {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { start: start.getTime(), end: end.getTime() };
}

function fmtTime(ms) {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function fmtDay(ms) {
  const d = new Date(ms);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

/** 会议：$DSH_HOME/meeting-assistant/meetings.json */
export function readMeetings(now = new Date()) {
  const root = path.join(dshHome(), 'meeting-assistant');
  const candidates = ['meetings.json', path.join('meetings', 'index.json')];
  let raw = null;
  let usedFile = '';
  for (const rel of candidates) {
    const file = path.join(root, rel);
    if (fs.existsSync(file)) {
      raw = readJson(file, null);
      usedFile = file;
      if (raw) break;
    }
  }
  if (!raw) return { ok: false, reason: 'meetings.json 不存在（会议助手尚未同步会议）', today: [], upcoming: [], raw: [] };

  const list = Array.isArray(raw)
    ? raw
    : Array.isArray(raw.meetings)
      ? raw.meetings
      : Array.isArray(raw.items)
        ? raw.items
        : Object.values(raw).filter((v) => v && typeof v === 'object');

  const { start, end } = dayRange(now);
  const horizon = start + 3 * 86400000;
  const today = [];
  const upcoming = [];

  for (const m of list) {
    if (!m || typeof m !== 'object') continue;
    const title = String(pick(m, ['title', 'subject', 'name', 'summary', 'topic']) || '未命名会议');
    const beginMs = toMs(pick(m, ['start_time', 'startTime', 'start_at', 'startAt', 'begin_time', 'beginTime', 'start', 'date_time', 'time']));
    const endMs = toMs(pick(m, ['end_time', 'endTime', 'end_at', 'endAt', 'end'])) || beginMs + 3600000;
    const id = String(pick(m, ['meeting_id', 'meetingId', 'id', 'minutes_id']) || title + beginMs);
    const row = { id, title, startMs: beginMs, endMs, time: beginMs ? fmtTime(beginMs) : '', day: beginMs ? fmtDay(beginMs) : '', source: 'meeting-assistant' };
    if (!beginMs) continue;
    if (beginMs >= start && beginMs < end) today.push(row);
    else if (beginMs >= end && beginMs < horizon) upcoming.push(row);
  }
  today.sort((a, b) => a.startMs - b.startMs);
  upcoming.sort((a, b) => a.startMs - b.startMs);
  return { ok: true, file: usedFile, today, upcoming };
}

/** Bot 群聊：$DSH_HOME/bot/state.json */
export function readBotChats(now = new Date()) {
  const file = path.join(dshHome(), 'bot', 'state.json');
  const data = readJson(file, null);
  if (!data) return { ok: false, reason: 'bot/state.json 不存在', items: [], unread: 0 };

  const rooms = data.rooms && typeof data.rooms === 'object' ? data.rooms : {};
  const exchanges = Array.isArray(data.exchanges) ? data.exchanges : [];
  const turns = data.exchangeTurns && typeof data.exchangeTurns === 'object' ? data.exchangeTurns : {};
  const { start } = dayRange(now);
  const items = [];

  const pushTurn = (t, roomLabel) => {
    if (!t || typeof t !== 'object') return;
    const at = toMs(pick(t, ['at', 'created_at', 'createdAt', 'ts', 'time', 'timestamp']));
    if (!at || at < start) return;
    const text = String(pick(t, ['text', 'content', 'body', 'message']) || '').trim();
    if (!text) return;
    const mentionsMe = /@我|@老板|@liao|@iminson/i.test(text) || t.mentionedMe === true || t.mention_me === true;
    items.push({
      id: String(t.id || t.turn_id || `${at}`),
      room: roomLabel,
      at,
      text: text.slice(0, 300),
      mentionsMe,
      sender: String(pick(t, ['sender_name', 'senderName', 'from', 'author', 'bot_name']) || ''),
    });
  };

  for (const [roomId, room] of Object.entries(rooms)) {
    const label = room?.name || room?.title || roomId;
    const list = Array.isArray(room?.turns) ? room.turns : Array.isArray(room?.messages) ? room.messages : [];
    for (const t of list) pushTurn(t, label);
  }
  for (const ex of exchanges) {
    const label = ex?.room_name || ex?.roomName || ex?.room_id || 'Bot 群聊';
    const list = Array.isArray(ex?.turns) ? ex.turns : [];
    for (const t of list) pushTurn(t, label);
    if (!list.length) pushTurn(ex, label);
  }
  for (const [exId, exTurns] of Object.entries(turns)) {
    const parent = exchanges.find((e) => String(e.id || e.exchange_id) === String(exId));
    const label = parent?.room_name || parent?.roomName || 'Bot 群聊';
    for (const t of Array.isArray(exTurns) ? exTurns : []) pushTurn(t, label);
  }

  items.sort((a, b) => b.at - a.at);
  const unread = Number(data.unreadCount || data?.prefs?.unreadCount || 0) || 0;
  return { ok: true, items: items.slice(0, 30), unread, total: items.length };
}

/**
 * 工作台任务：$DSH_HOME/workbench-bot/state.json
 *
 * 兼容两代状态结构：
 *  - v1/v2：run.status（字符串）+ run.goal/title + conversations[id].name
 *  - v3   ：run.state（字符串，终态含 delivered）+ run.taskSpec.goal/title + conversations[id].title
 *
 * 只输出「还需要人看」的任务：终态（delivered/succeeded/...）与预算耗尽类僵尸 run 全部丢掉，
 * 否则历史交付记录会被整屏列成「在跑的任务」。
 */
const TERMINAL_STATES = new Set([
  'succeeded', 'success', 'completed', 'complete', 'done', 'delivered', 'finished', 'closed',
  'failed_terminal', 'failed', 'cancelled', 'canceled', 'superseded', 'aborted', 'expired', 'timeout', 'timed_out',
]);
/** 这些 blocked 原因不可恢复，等价于死亡状态 */
const DEAD_BLOCK_REASONS = new Set([
  'budget_exhausted', 'cancelled_by_user', 'user_cancelled', 'superseded', 'deleted_queue_blocked', 'expired',
]);
/** 活跃态（用于未知/空状态时的兜底判定） */
const LIVE_STATES = new Set([
  'running', 'active', 'executing', 'in_progress', 'planning', 'preparing', 'queued', 'pending',
  'waiting_user', 'waiting', 'blocked', 'paused', 'retrying', 'failed_retryable', 'resumed',
]);
const STATE_LABELS = {
  running: '进行中', active: '进行中', executing: '进行中', in_progress: '进行中',
  planning: '规划中', preparing: '准备中', queued: '排队中', pending: '排队中',
  waiting_user: '待你确认', waiting: '待你确认', blocked: '受阻', paused: '已暂停',
  retrying: '重试中', failed_retryable: '可重试', resumed: '已恢复',
};
/** 默认只看最近 24h 的活动；waiting_user 不受时间窗限制（永远要人处理） */
const DEFAULT_WINDOW_HOURS = 24;
const MAX_PENDING = 8;

function runState(run) {
  const raw = typeof run?.state === 'string' ? run.state : typeof run?.status === 'string' ? run.status : '';
  return String(raw || '').trim().toLowerCase();
}

function clip(text, max) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

export function readWorkbench(now = new Date()) {
  const file = path.join(dshHome(), 'workbench-bot', 'state.json');
  const data = readJson(file, null);
  if (!data) return { ok: false, reason: 'workbench-bot/state.json 不存在', pending: [], questions: [], runs: [], filtered: 0 };

  const cfg = loadConfig();
  const windowHours = Number(cfg?.sources?.workbenchWindowHours) > 0 ? Number(cfg.sources.workbenchWindowHours) : DEFAULT_WINDOW_HOURS;
  const maxAgeMs = windowHours * 3600000;

  const conversations = data.conversations && typeof data.conversations === 'object' ? data.conversations : {};
  const runs = data.runs && typeof data.runs === 'object' ? data.runs : {};
  const questions = data.questions && typeof data.questions === 'object' ? data.questions : {};
  const pending = [];
  const openQuestions = [];
  let filtered = 0;

  for (const [id, run] of Object.entries(runs)) {
    const state = runState(run);

    // 1) 终态 / 僵尸 run 直接丢弃
    if (TERMINAL_STATES.has(state)) {
      filtered += 1;
      continue;
    }
    if (state === 'blocked' && DEAD_BLOCK_REASONS.has(String(run?.blockedReason || '').trim().toLowerCase())) {
      filtered += 1;
      continue;
    }
    // 2) 状态缺失（旧结构或异常写入）：只有明确有活跃信号才保留
    if (!state) {
      if (!run?.activeSince && !run?.activeSessionId) {
        filtered += 1;
        continue;
      }
    } else if (!LIVE_STATES.has(state)) {
      filtered += 1;
      continue;
    }

    const at = toMs(pick(run, ['createdAt', 'created_at', 'startedAt', 'started_at', 'updatedAt', 'updated_at'])) || 0;
    // 3) 时间窗：待人工确认的永远保留，其余超过窗口的视为历史残留
    const needsHuman = state === 'waiting_user' || state === 'waiting';
    if (!needsHuman && at && now.getTime() - at > maxAgeMs) {
      filtered += 1;
      continue;
    }

    const spec = run?.taskSpec && typeof run.taskSpec === 'object' ? run.taskSpec : {};
    const title = clip(pick(spec, ['title', 'goal']) || pick(run, ['goal', 'title', 'summary', 'intent']) || run?.sourceMessageBody?.text || '未命名任务', 60);
    const conv = conversations[run?.conversationId] || {};
    pending.push({
      id,
      title,
      status: STATE_LABELS[state] || state || '进行中',
      rawState: state,
      reason: String(run?.blockedReason || ''),
      conversation: String(conv?.title || conv?.name || run?.conversationId || ''),
      at,
    });
  }

  // 需要人处理的排前面（待确认 > 受阻/可重试 > 运行中），同级按时间倒序
  const weight = (s) => (s === '待你确认' ? 0 : s === '受阻' || s === '可重试' ? 1 : 2);
  pending.sort((a, b) => weight(a.status) - weight(b.status) || b.at - a.at);

  for (const [id, q] of Object.entries(questions)) {
    if (q?.answeredAt || q?.answer) continue;
    openQuestions.push({
      id,
      text: String(pick(q, ['text', 'question', 'prompt']) || '').slice(0, 240),
      at: toMs(pick(q, ['createdAt', 'created_at', 'at'])) || 0,
    });
  }
  return {
    ok: true,
    pending: pending.slice(0, MAX_PENDING),
    pendingTotal: pending.length,
    filtered,
    questions: openQuestions,
    conversations: Object.keys(conversations).length,
    windowHours,
  };
}

/** 系统告警：systemctl --failed（无 systemd 或超时则跳过） */
export function readAlerts() {
  return new Promise((resolve) => {
    execFile('systemctl', ['--failed', '--no-legend', '--no-pager', '--plain'], { timeout: 4000 }, (error, stdout) => {
      if (error) {
        resolve({ ok: false, reason: 'systemctl 不可用', units: [] });
        return;
      }
      const units = String(stdout || '')
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => {
          const parts = l.split(/\s+/);
          return { unit: parts[0] || l, detail: parts.slice(1).join(' ') };
        })
        .filter((u) => u.unit.endsWith('.service') && !/dsh-exitnode|dsh-gpt-proxy-egress|dsh-model-egress|dsh-hardening/i.test(u.unit))
        .slice(0, 8);
      resolve({ ok: true, units });
    });
  });
}

/** 手动待办 + 逾期结转 */
export function readTodos(now = new Date()) {
  const state = loadState();
  const { start } = dayRange(now);
  const todos = Array.isArray(state.todos) ? state.todos : [];
  const open = todos.filter((t) => !t.done);
  const today = open.filter((t) => {
    const due = toMs(t.due);
    return !due || (due >= start && due < start + 86400000);
  });
  const overdue = open.filter((t) => {
    const due = toMs(t.due);
    return due && due < start;
  });
  return {
    ok: true,
    today: today.map((t) => ({ id: t.id, text: t.text, due: t.due || '', priority: t.priority || '' })),
    overdue: overdue.map((t) => ({ id: t.id, text: t.text, due: t.due || '', priority: t.priority || '', daysLate: Math.max(1, Math.floor((start - toMs(t.due)) / 86400000)) })),
    doneToday: todos.filter((t) => t.done && toMs(t.doneAt) >= start).length,
  };
}

/** 项目/供应链进度（扩展位：先在 config.projects 里手填，未来接产品库/表格） */
export function readProjects() {
  const cfg = loadConfig();
  if (!cfg?.sources?.projects) return { ok: true, enabled: false, items: [] };
  const items = (cfg.projects || []).filter((p) => p && p.enabled !== false);
  return { ok: true, enabled: true, items };
}

export async function collect(now = new Date()) {
  const cfg = loadConfig();
  const flags = cfg?.sources || {};
  const [alerts] = await Promise.all([
    flags.alerts ? readAlerts() : Promise.resolve({ ok: true, units: [] }),
  ]);
  const meetings = flags.meetings ? readMeetings(now) : { ok: true, today: [], upcoming: [] };
  const chats = flags.botChats ? readBotChats(now) : { ok: true, items: [] };
  const workbench = flags.workbench ? readWorkbench(now) : { ok: true, pending: [], questions: [] };
  const todos = flags.manual ? readTodos(now) : { ok: true, today: [], overdue: [] };
  const projects = readProjects();

  const notices = [
    ...(chats.items || []).filter((i) => i.mentionsMe).slice(0, 6),
    ...(workbench.questions || []).map((q) => ({ id: q.id, text: q.text, room: '工作台提问', at: q.at, mentionsMe: true })),
  ].slice(0, 10);

  const counts = {
    meetings: (meetings.today || []).length,
    upcoming: (meetings.upcoming || []).length,
    notices: notices.length,
    todos: (todos.today || []).length,
    overdue: (todos.overdue || []).length,
    pendingRuns: workbench.pendingTotal !== undefined ? workbench.pendingTotal : (workbench.pending || []).length,
    alerts: (alerts.units || []).length,
  };

  return {
    generatedAt: new Date().toISOString(),
    date: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`,
    meetings,
    chats,
    workbench,
    todos,
    alerts,
    projects,
    notices,
    counts,
    health: {
      meetings: meetings.ok ? 'ok' : meetings.reason || 'empty',
      chats: chats.ok ? 'ok' : chats.reason || 'empty',
      workbench: workbench.ok ? 'ok' : workbench.reason || 'empty',
      alerts: alerts.ok ? 'ok' : alerts.reason || 'empty',
    },
  };
}
