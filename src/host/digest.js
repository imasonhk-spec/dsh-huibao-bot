/**
 * 汇报稿生成：先出确定性模板稿（永远可用），再按配置用模型润色成"重点一句 + 清单"。
 * 润色失败（无 Key / 超时 / 报错）一律回退模板稿，保证"总有一句话能念出来"。
 */
import { loadConfig, resolveDigestModel } from './config.js';

function pad(n) {
  return String(n).padStart(2, '0');
}

export function nowLabel(now = new Date()) {
  return `${now.getHours() >= 12 ? '下午' : '上午'}${now.getHours() % 12 === 0 ? 12 : now.getHours() % 12}点`;
}

/** 从聚合数据里提炼"最要紧的一件事" */
export function pickHeadline(digest) {
  const c = digest.counts || {};
  if (c.overdue > 0) return `有 ${c.overdue} 件事已逾期，建议先处理`;
  if (c.meetings > 0) {
    const first = digest.meetings.today[0];
    return `今天 ${c.meetings} 个会，第一个是 ${first.time} 的「${first.title}」`;
  }
  if (c.notices > 0) return `有 ${c.notices} 条通知在等你处理`;
  if (c.todos > 0) return `今天有 ${c.todos} 项待办，没有会议`;
  if (c.alerts > 0) return `有 ${c.alerts} 个服务异常需要看一眼`;
  return '今天没有会议和待办，可以安排整块时间';
}

/** 确定性模板稿（不依赖任何模型） */
export function buildTemplateDigest(digest) {
  const now = new Date();
  const headline = pickHeadline(digest);
  const lines = [];
  lines.push(`${nowLabel(now)}好，${headline}。`);

  const m = digest.meetings || {};
  if ((m.today || []).length) {
    lines.push(`今天会议 ${m.today.length} 个：`);
    for (const item of m.today.slice(0, 6)) lines.push(`${item.time}，${item.title}。`);
  }
  if ((digest.todos?.overdue || []).length) {
    lines.push(`逾期 ${digest.todos.overdue.length} 项：`);
    for (const t of digest.todos.overdue.slice(0, 4)) lines.push(`${t.text}，已逾期 ${t.daysLate} 天。`);
  }
  if ((digest.todos?.today || []).length) {
    lines.push(`今日待办 ${digest.todos.today.length} 项：`);
    for (const t of digest.todos.today.slice(0, 6)) lines.push(`${t.text}${t.due ? `，截止 ${t.due}` : ''}。`);
  }
  if ((digest.notices || []).length) {
    lines.push(`需要你回应的通知 ${digest.notices.length} 条：`);
    for (const n of digest.notices.slice(0, 4)) lines.push(`${n.room ? n.room + '：' : ''}${n.text}`.slice(0, 120) + '。');
  }
  if ((digest.workbench?.pending || []).length) {
    const pr = digest.workbench.pending;
    const hot = pr.filter((r) => r.status === '待你确认' || r.status === '受阻' || r.status === '可重试').length;
    lines.push(
      `工作台还有 ${pr.length} 个任务待处理${hot ? `（其中 ${hot} 个卡住等你）` : ''}：${pr.slice(0, 3).map((r) => r.title).join('、')}。`,
    );
  }
  if ((digest.alerts?.units || []).length) {
    lines.push(`服务异常 ${digest.alerts.units.length} 个：${digest.alerts.units.slice(0, 3).map((u) => u.unit).join('、')}。`);
  }
  if ((m.upcoming || []).length) {
    lines.push(`往后三天还有 ${m.upcoming.length} 个会，最近的是 ${m.upcoming[0].day} ${m.upcoming[0].time} 的「${m.upcoming[0].title}」。`);
  }
  if ((digest.projects?.items || []).length) {
    lines.push('项目进度：' + digest.projects.items.slice(0, 4).map((p) => `${p.name}${p.status ? '，' + p.status : ''}`).join('；') + '。');
  }
  if (lines.length <= 1) lines.push('没有需要特别提醒的事。');

  return {
    headline,
    speakText: lines.join(''),
    sections: buildSections(digest),
    polished: false,
  };
}

export function buildSections(digest) {
  return {
    meetings: digest.meetings?.today || [],
    upcoming: digest.meetings?.upcoming || [],
    overdue: digest.todos?.overdue || [],
    todos: digest.todos?.today || [],
    notices: digest.notices || [],
    pendingRuns: digest.workbench?.pending || [],
    alerts: digest.alerts?.units || [],
    projects: digest.projects?.items || [],
  };
}

async function callModel(messages, model, timeoutMs) {
  const endpoint = `${String(model.baseUrl || '').replace(/\/+$/, '')}${/\/chat\/completions$/.test(model.baseUrl || '') ? '' : '/chat/completions'}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || 30000);
  try {
    // 推理型模型会把 token 花在 reasoning 上，700 容易把 content 挤成空串 → 放宽上限并允许一次重试
    const payload = { model: model.model, messages, temperature: 0.3, max_tokens: 1500 };
    let text = '';
    let lastError = '';
    for (let attempt = 0; attempt < 2 && !text; attempt += 1) {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${model.apiKey}` },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!res.ok) return { ok: false, error: `http_${res.status}` };
      const data = await res.json();
      const choice = data?.choices?.[0];
      text = String(choice?.message?.content || choice?.text || '').trim();
      if (!text) lastError = choice?.finish_reason === 'length' ? 'truncated_no_content' : 'empty_choice';
    }
    return { ok: Boolean(text), text, error: text ? '' : lastError || 'empty_choice' };
  } catch (error) {
    return { ok: false, error: error?.name === 'AbortError' ? 'timeout' : String(error?.message || error) };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 生成最终汇报稿。
 * @param digest collect() 的结果
 * @param opts   { mode: 'brief'|'full'|'reminder', focus?: string }
 */
export async function buildDigest(digest, opts = {}) {
  const base = buildTemplateDigest(digest);
  const cfg = loadConfig();
  if (!cfg?.digestModel?.polish) return base;
  const model = resolveDigestModel(cfg);
  if (!model?.apiKey) return { ...base, polishSkipped: 'no_model_key' };

  const facts = JSON.stringify(
    {
      today: digest.date,
      meetings: (digest.meetings?.today || []).map((x) => ({ time: x.time, title: x.title })),
      upcoming: (digest.meetings?.upcoming || []).slice(0, 5).map((x) => ({ day: x.day, time: x.time, title: x.title })),
      overdue: (digest.todos?.overdue || []).map((x) => ({ text: x.text, daysLate: x.daysLate })),
      todos: (digest.todos?.today || []).map((x) => ({ text: x.text, due: x.due })),
      notices: (digest.notices || []).slice(0, 6).map((x) => ({ room: x.room, text: x.text })),
      pendingRuns: (digest.workbench?.pending || []).slice(0, 4).map((x) => x.title),
      alerts: (digest.alerts?.units || []).map((x) => x.unit),
      projects: (digest.projects?.items || []).map((x) => ({ name: x.name, status: x.status })),
    },
    null,
    0,
  );

  const modeHint =
    opts.mode === 'reminder'
      ? `这是一条会前提醒，只用两句话：先说 ${opts.focus || '即将开始的会议'} 还有几分钟开始，再说需要准备什么。`
      : '这是当天的口头汇报，先一句话给重点（最要紧的一件事），再按优先级报清单明细，总长控制在 200 字内。';

  const messages = [
    {
      role: 'system',
      content:
        '你是中文语音助理，负责把结构化日程数据说成一段适合朗读的口语汇报。要求：' +
        '1) 只使用给定事实，绝不编造时间、人名、数字、链接；2) 句子短，不用 Markdown、列表符号、括号、表情；' +
        '3) 数字读法自然（如 10点30分）；4) 事实为空就直说没有，不要凑内容；' +
        modeHint,
    },
    { role: 'user', content: `事实数据（JSON）：${facts}` },
  ];

  const result = await callModel(messages, model, cfg.digestModel.timeoutMs);
  if (!result.ok) return { ...base, polishError: result.error };
  const text = result.text.replace(/[*#`]/g, '').replace(/\n{2,}/g, '\n').trim();
  return {
    headline: text.split(/[。！？\n]/)[0] || base.headline,
    speakText: text,
    sections: base.sections,
    polished: true,
  };
}
