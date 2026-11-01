/**
 * "小歪 Bot" 对话：把今日聚合事实作为上下文，回答「今天要干什么」这类问题。
 * 输出遵守「重点一句 + 清单」的口径，且可直接交给设备朗读。
 */
import { loadConfig, resolveDigestModel, loadState, updateState } from './config.js';
import { collect } from './sources.js';
import { buildDigest } from './digest.js';
import { speak } from './xiaowai.js';

function factsOf(digest) {
  return JSON.stringify(
    {
      today: digest.date,
      meetings: (digest.meetings?.today || []).map((x) => ({ time: x.time, title: x.title })),
      upcoming: (digest.meetings?.upcoming || []).slice(0, 6).map((x) => ({ day: x.day, time: x.time, title: x.title })),
      overdue: (digest.todos?.overdue || []).map((x) => ({ text: x.text, daysLate: x.daysLate })),
      todos: (digest.todos?.today || []).map((x) => ({ text: x.text, due: x.due })),
      notices: (digest.notices || []).slice(0, 8).map((x) => ({ room: x.room, text: x.text })),
      pendingRuns: (digest.workbench?.pending || []).slice(0, 5).map((x) => ({ title: x.title, status: x.status })),
      alerts: (digest.alerts?.units || []).map((x) => x.unit),
      projects: (digest.projects?.items || []).map((x) => ({ name: x.name, status: x.status })),
      sourceHealth: digest.health,
    },
    null,
    0,
  );
}

const SYSTEM = [
  '你是「小歪」的工作汇报助手，回答风格：先一句话给最要紧的结论，再用短句列清单（最多 6 条）。',
  '严格要求：只使用给定事实，不编造时间、人名、链接、数字；事实里没有的就说"没有这项数据"。',
  '回答适合直接朗读：不用 Markdown、列表符号、括号、表情。必要时提醒用户数据源为空。',
].join('');

export async function chat(userText, opts = {}) {
  const text = String(userText || '').trim();
  if (!text) return { ok: false, error: 'empty_text' };
  const cfg = loadConfig();
  const digest = await collect();
  const model = resolveDigestModel(cfg);

  let answer = '';
  let usedModel = false;
  if (model?.apiKey) {
    const endpoint = `${String(model.baseUrl || '').replace(/\/+$/, '')}${/\/chat\/completions$/.test(model.baseUrl || '') ? '' : '/chat/completions'}`;
    const history = (loadState().chat || []).slice(-8).map((m) => ({ role: m.role, content: m.content }));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Number(cfg?.digestModel?.timeoutMs || 30000));
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${model.apiKey}` },
        body: JSON.stringify({
          model: model.model,
          temperature: 0.3,
          max_tokens: 700,
          messages: [
            { role: 'system', content: SYSTEM },
            { role: 'system', content: `当前事实数据（JSON）：${factsOf(digest)}` },
            ...history,
            { role: 'user', content: text },
          ],
        }),
        signal: controller.signal,
      });
      if (res.ok) {
        const data = await res.json();
        answer = data?.choices?.[0]?.message?.content || '';
        usedModel = Boolean(answer);
      }
    } catch {
      /* 回退到模板口径 */
    } finally {
      clearTimeout(timer);
    }
  }

  if (!answer) {
    const fallback = await buildDigest(digest, { mode: 'full' });
    answer = fallback.speakText;
  }

  updateState((s) => {
    s.chat.push({ role: 'user', content: text.slice(0, 500), at: Date.now() });
    s.chat.push({ role: 'assistant', content: answer.slice(0, 2000), at: Date.now() });
    if (s.chat.length > 60) s.chat = s.chat.slice(-60);
    return s;
  });

  let speakResult = null;
  if (opts.speak) speakResult = await speak(answer, { scene: 'chat' });

  return { ok: true, answer, usedModel, speak: speakResult, digest };
}

export function chatHistory(limit = 30) {
  const list = loadState().chat || [];
  return list.slice(-limit).map((m) => ({ role: m.role, content: m.content, at: m.at }));
}

export function clearChat() {
  updateState((s) => {
    s.chat = [];
    return s;
  });
  return { ok: true };
}
