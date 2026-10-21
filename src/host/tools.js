/**
 * 向 DSH 注册工具：让工作台里的任意 Agent / Bot 也能"查今日汇报、让它念出来"。
 */
import { collect } from './sources.js';
import { buildDigest } from './digest.js';
import { speak, snapshot } from './xiaowai.js';
import { updateState } from './config.js';

const TEXT_OUTPUT = { schema: {}, render: (_args, value) => [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }] };

export function registerTools(ctx) {
  const tools = ctx.tools || (ctx.get && ctx.get('tools'));
  if (!tools || typeof tools.register !== 'function') return () => {};

  const defs = [
    {
      name: 'huibao_daily_digest',
      description:
        '生成今天的口头汇报稿（重点一句 + 清单）：包含今日会议、未处理通知、今日待办、逾期结转、未来几天预告、系统告警。只读事实，不编造。',
      parameters: { type: 'object', properties: { mode: { type: 'string', enum: ['full', 'brief'] } }, additionalProperties: false },
      execute: async (args = {}) => {
        const digest = await collect();
        const built = await buildDigest(digest, { mode: args.mode || 'full' });
        return { headline: built.headline, speakText: built.speakText, sections: built.sections, counts: digest.counts, health: digest.health };
      },
    },
    {
      name: 'huibao_today',
      description: '返回今天的结构化日程数据（会议/通知/待办/逾期/告警），用于回答"今天要干什么"这类追问。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      execute: async () => {
        const digest = await collect();
        return {
          date: digest.date,
          counts: digest.counts,
          meetings: digest.meetings?.today || [],
          upcoming: digest.meetings?.upcoming || [],
          overdue: digest.todos?.overdue || [],
          todos: digest.todos?.today || [],
          notices: digest.notices || [],
          pendingRuns: digest.workbench?.pending || [],
          alerts: digest.alerts?.units || [],
          health: digest.health,
        };
      },
    },
    {
      name: 'huibao_speak',
      description: '让桌面机器人小歪把一段话说出来（TTS 推送到设备扬声器）。不传 text 时播报今日汇报稿。',
      parameters: {
        type: 'object',
        properties: { text: { type: 'string', description: '要朗读的文本；留空则播报今日汇报' }, deviceId: { type: 'string' } },
        additionalProperties: false,
      },
      execute: async (args = {}) => {
        let text = String(args.text || '').trim();
        if (!text) {
          const built = await buildDigest(await collect(), { mode: 'full' });
          text = built.speakText;
        }
        const result = await speak(text, { deviceId: args.deviceId });
        updateState((s) => {
          if (result.ok) s.speakLog.push({ at: Date.now(), kind: 'tool', text: text.slice(0, 500) });
          return s;
        });
        return { ok: result.ok, deviceId: result.deviceId, text, error: result.error || '' };
      },
    },
    {
      name: 'huibao_add_todo',
      description: '往今日待办里加一条（手动来源，落盘 $DSH_HOME/huibao-bot/state.json）。',
      parameters: {
        type: 'object',
        properties: { text: { type: 'string' }, due: { type: 'string', description: 'YYYY-MM-DD 或 YYYY-MM-DD HH:mm，可留空' }, priority: { type: 'string' } },
        required: ['text'],
        additionalProperties: false,
      },
      execute: async (args = {}) => {
        const text = String(args.text || '').trim();
        if (!text) return { ok: false, error: 'missing_text' };
        const due = String(args.due || '').trim();
        const item = {
          id: 'todo-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
          text,
          due: /^\d{4}-\d{2}-\d{2}$/.test(due) ? `${due}T23:59` : due.replace(' ', 'T'),
          priority: args.priority || '',
          done: false,
          createdAt: Date.now(),
        };
        updateState((s) => {
          s.todos.push(item);
          return s;
        });
        return { ok: true, item };
      },
    },
    {
      name: 'huibao_device_status',
      description: '查桌面机器人小歪服务与设备状态（服务是否活着、哪些设备在线、走的串口还是 WiFi）。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      execute: async () => snapshot(),
    },
  ];

  const disposers = [];
  for (const def of defs) {
    try {
      const dispose = tools.register({
        name: def.name,
        description: def.description,
        parameters: def.parameters,
        output: TEXT_OUTPUT,
        execute: def.execute,
      });
      if (typeof dispose === 'function') disposers.push(dispose);
    } catch (error) {
      console.error(`[huibao] 工具注册失败 ${def.name}:`, error?.message || error);
    }
  }
  return () => {
    for (const d of disposers) {
      try {
        d();
      } catch {
        /* ignore */
      }
    }
  };
}
