window.__ModuleLoader__.load({
  id: '@local/huibao-bot',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const { useState, useEffect, useCallback, useRef } = React;
    const BASE = '/api/huibao-bot';

    /* ---------------- API ---------------- */
    async function api(method, path, body) {
      const res = await fetch(BASE + path, {
        method,
        credentials: 'same-origin',
        headers: body ? { 'content-type': 'application/json' } : {},
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw Object.assign(new Error(data.error || 'HTTP ' + res.status), data);
      return data;
    }

    /* ---------------- 样式（只注入一次） ---------------- */
    const CSS = `
.hbb{--b:#2f6df6;--d:#2058d4;--s:#eef4ff;--ink:#1f2d45;--mut:#7d8da4;--line:#e5edf7;box-sizing:border-box;
  height:100%;width:100%;display:flex;flex-direction:column;font:12px/1.6 Inter,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);background:#fff}
.hbb *{box-sizing:border-box}
.hbb button{font:inherit;border:0;cursor:pointer;background:transparent}
.hbb button:disabled{opacity:.5;cursor:default}
.hbb-head{flex-shrink:0;display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid var(--line)}
.hbb-head h1{flex:1;margin:0;font-size:14px;font-weight:750;letter-spacing:.2px}
.hbb-dot{width:8px;height:8px;border-radius:50%;background:#c3ccd9;flex:0 0 8px}
.hbb-dot.on{background:#18b77e;box-shadow:0 0 0 3px #18b77e22}
.hbb-dot.err{background:#e2606f}
.hbb-tabs{flex-shrink:0;display:flex;gap:2px;padding:6px 10px;border-bottom:1px solid var(--line);background:#fafcff}
.hbb-tab{padding:5px 10px;border-radius:7px;color:#6b7c94;font-size:11.5px;font-weight:600}
.hbb-tab.on{color:var(--b);background:var(--s)}
.hbb-body{flex:1;min-height:0;overflow:auto;padding:12px 14px}
.hbb-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(74px,1fr));gap:7px;margin-bottom:10px}
.hbb-card{padding:8px 9px;border:1px solid var(--line);border-radius:10px;background:#fbfdff}
.hbb-card b{display:block;font-size:17px;font-weight:750;line-height:1.2;color:var(--ink)}
.hbb-card span{display:block;margin-top:2px;font-size:10px;color:var(--mut)}
.hbb-card.warn b{color:#d4820f}
.hbb-card.bad b{color:#cf4d5c}
.hbb-say{padding:10px 12px;border:1px solid #d8e6ff;border-radius:11px;background:linear-gradient(180deg,#f7faff,#f2f7ff)}
.hbb-say strong{display:block;margin-bottom:4px;font-size:12.5px}
.hbb-say p{margin:0;color:#5a6e8c;font-size:11.5px;white-space:pre-wrap;overflow-wrap:anywhere}
.hbb-row{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:9px 0}
.hbb-btn{padding:6px 11px;border:1px solid #d8e4f7;border-radius:8px;color:#5b708f;background:#f7faff;font-size:11.5px;font-weight:600}
.hbb-btn:hover{border-color:#b9d0ff;color:var(--b)}
.hbb-btn.primary{color:#fff;border-color:var(--b);background:var(--b)}
.hbb-btn.primary:hover{background:var(--d)}
.hbb-btn.ghost{border-color:transparent;background:transparent;color:#8494a8}
.hbb-sec{margin:14px 0 6px;font-size:10.5px;font-weight:750;letter-spacing:.06em;color:#9aa8bb}
.hbb-item{display:flex;align-items:flex-start;gap:8px;padding:7px 0;border-bottom:1px dashed #eef2f8}
.hbb-item:last-child{border-bottom:0}
.hbb-when{flex:0 0 44px;color:var(--b);font-size:11px;font-weight:700;font-variant-numeric:tabular-nums}
.hbb-txt{flex:1;min-width:0;font-size:11.5px;overflow-wrap:anywhere}
.hbb-txt small{display:block;color:var(--mut);font-size:10px}
.hbb-tag{flex:0 0 auto;padding:1px 6px;border-radius:5px;font-size:9.5px;font-weight:700;color:#5b708f;background:#eef3fa}
.hbb-tag.red{color:#c04050;background:#fdeef0}
.hbb-tag.blue{color:#3a6fd8;background:#e9f1ff}
.hbb-empty{padding:16px 10px;color:#93a1b5;font-size:11.5px;text-align:center;border:1px dashed #e2eaf5;border-radius:10px}
.hbb-chat{display:flex;flex-direction:column;height:100%;min-height:0}
.hbb-msgs{flex:1;min-height:0;overflow:auto;padding:2px}
.hbb-msg{max-width:86%;margin:0 0 10px;padding:8px 10px;border-radius:4px 12px 12px 12px;background:#f1f4f8;color:#40516b;font-size:11.5px;white-space:pre-wrap;overflow-wrap:anywhere}
.hbb-msg.me{margin-left:auto;border-radius:12px 4px 12px 12px;color:#fff;background:linear-gradient(135deg,#4b82fa,#2c65e7)}
.hbb-input{flex-shrink:0;margin-top:8px;border-top:1px solid var(--line);padding-top:8px}
.hbb-input textarea{width:100%;height:56px;resize:none;padding:8px 9px;border:1px solid #dce5f2;border-radius:9px;background:#fbfdff;font:inherit;font-size:11.5px}
.hbb-field{margin:0 0 10px}
.hbb-field label{display:block;margin-bottom:3px;color:#6b7c94;font-size:10.5px;font-weight:700}
.hbb-field input[type=text],.hbb-field input[type=password],.hbb-field input[type=number],.hbb-field select,.hbb-field input[type=time]{width:100%;padding:6px 8px;border:1px solid #dce5f2;border-radius:8px;background:#fbfdff;font:inherit;font-size:11.5px;color:#40516b}
.hbb-field .hint{margin-top:3px;color:#9aa8bb;font-size:10px}
.hbb-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.hbb-switch{display:flex;align-items:center;gap:7px;font-size:11.5px;color:#4c5f7c}
.hbb-err{padding:8px 10px;margin-bottom:8px;border-radius:8px;color:#b23a4c;background:#fdeef0;font-size:11px;white-space:pre-wrap}
.hbb-ok{padding:8px 10px;margin-bottom:8px;border-radius:8px;color:#1d7a56;background:#eaf8f1;font-size:11px}
.hbb-todo{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px dashed #eef2f8}
.hbb-todo.done .hbb-txt{color:#a6b2c2;text-decoration:line-through}
.hbb-chk{width:16px;height:16px;flex:0 0 16px;border:1.5px solid #c6d3e4;border-radius:5px;display:grid;place-items:center;font-size:10px;color:#fff}
.hbb-chk.on{border-color:var(--b);background:var(--b)}
.hbb-del{color:#b6c2d2;font-size:14px;padding:0 4px}
.hbb-del:hover{color:#cf4d5c}
/* ── 同源嵌入（DSH 工作台 × 小歪 R6 控制台 /xw/）────────────── */
.hbb-embed{position:relative;height:100%;width:100%;min-height:0;background:transparent}
.hbb-embed-frame{display:block;width:100%;height:100%;border:0;background:transparent}
.hbb-panelmode{position:relative}
.hbb-fab{position:absolute;right:14px;bottom:14px;z-index:30;padding:5px 12px;border-radius:999px;
  font:inherit;font-size:11px;font-weight:600;line-height:1.6;cursor:pointer;
  color:var(--dsw-alias-label-secondary,#6b7c94);
  background:var(--dsw-alias-bg-layer-1,rgba(255,255,255,.92));
  border:1px solid var(--dsw-alias-border-l2,rgba(0,0,0,.12));
  box-shadow:0 2px 10px rgba(10,20,40,.12);opacity:.62;
  transition:opacity .15s,color .15s,border-color .15s}
.hbb-fab:hover{opacity:1;color:var(--dsw-alias-brand-primary-new-colorprimary-new-color,#2f6df6);
  border-color:var(--dsw-alias-brand-primary-new-colorprimary-new-color,#b9d0ff)}
`;
    function useStyle() {
      useEffect(() => {
        if (document.querySelector('style[data-huibao]')) return;
        const el = document.createElement('style');
        el.setAttribute('data-huibao', '1');
        el.textContent = CSS;
        document.head.appendChild(el);
      }, []);
    }

    /* ---------------- 图标 ---------------- */
    const Icon = (props) =>
      h(
        'svg',
        { viewBox: '0 0 48 48', width: props?.size || 22, height: props?.size || 22, 'aria-hidden': true },
        h('path', { d: 'M10 16h16l10-6v22l-10-6H10z', fill: '#eaf2ff', stroke: '#2f6df6', strokeWidth: 2.4, strokeLinejoin: 'round' }),
        h('path', { d: 'M14 26v7h4l3 6h4l-3-6', fill: 'none', stroke: '#2f6df6', strokeWidth: 2.4, strokeLinecap: 'round' }),
        h('path', { d: 'M40 15c2 2.6 2 9 0 11', fill: 'none', stroke: '#ff8193', strokeWidth: 2.4, strokeLinecap: 'round' }),
      );

    /* ---------------- 小组件 ---------------- */
    const Dot = ({ state }) => h('i', { className: 'hbb-dot ' + (state === 'ok' ? 'on' : state ? 'err' : '') });
    const secTitle = (t) => h('div', { className: 'hbb-sec' }, t);
    const empty = (t) => h('div', { className: 'hbb-empty' }, t);

    function Cards({ counts }) {
      if (!counts) return null;
      const items = [
        { k: '会议', v: counts.meetings, cls: '' },
        { k: '待办', v: counts.todos, cls: '' },
        { k: '逾期', v: counts.overdue, cls: counts.overdue ? 'bad' : '' },
        { k: '通知', v: counts.notices, cls: counts.notices ? 'warn' : '' },
        { k: '任务', v: counts.pendingRuns, cls: '' },
        { k: '告警', v: counts.alerts, cls: counts.alerts ? 'warn' : '' },
      ];
      return h('div', { className: 'hbb-cards' }, items.map((i) => h('div', { key: i.k, className: 'hbb-card ' + i.cls }, h('b', {}, String(i.v ?? 0)), h('span', {}, i.k))));
    }

    /* ---------------- 今日汇报 ---------------- */
    function TodayTab({ digest, status, busy, onRefresh, onSpeak }) {
      useStyle();
      return h(
        'div',
        {},
        h(Cards, { counts: digest?.counts }),
        digest
          ? h('div', { className: 'hbb-say' }, h('strong', {}, digest.headline || '今日汇报'), h('p', {}, digest.speakText || ''))
          : empty('正在汇总今日数据…'),
        h(
          'div',
          { className: 'hbb-row' },
          h('button', { className: 'hbb-btn primary', disabled: busy, onClick: () => onSpeak() }, busy ? '播报中…' : '念给我听'),
          h('button', { className: 'hbb-btn', disabled: busy, onClick: onRefresh }, '刷新'),
          digest?.polished === false && digest?.polishError ? h('span', { style: { color: '#9aa8bb', fontSize: 10 } }, '模型润色失败：' + digest.polishError) : null,
        ),
        digest?.health && Object.values(digest.health).some((v) => v !== 'ok') ? secTitle('数据源状态') : null,
        digest?.health && Object.values(digest.health).some((v) => v !== 'ok')
          ? h(
              'div',
              { style: { fontSize: 10.5, color: '#8494a8' } },
              Object.entries(digest.health)
                .filter(([, v]) => v !== 'ok')
                .map(([k, v]) => h('div', { key: k }, `${k}：${v}`)),
            )
          : null,
        digest?.sections?.overdue?.length ? secTitle('逾期结转') : null,
        (digest?.sections?.overdue || []).map((t) =>
          h('div', { className: 'hbb-item', key: t.id }, h('div', { className: 'hbb-when' }, `${t.daysLate}天`), h('div', { className: 'hbb-txt' }, t.text), h('span', { className: 'hbb-tag red' }, '逾期')),
        ),
        digest?.sections?.meetings?.length ? secTitle('今日会议') : null,
        (digest?.sections?.meetings || []).map((m) =>
          h('div', { className: 'hbb-item', key: m.id }, h('div', { className: 'hbb-when' }, m.time), h('div', { className: 'hbb-txt' }, m.title), h('span', { className: 'hbb-tag blue' }, '会议')),
        ),
        digest?.sections?.todos?.length ? secTitle('今日待办') : null,
        (digest?.sections?.todos || []).map((t) =>
          h('div', { className: 'hbb-item', key: t.id }, h('div', { className: 'hbb-when' }, t.due ? String(t.due).slice(11, 16) || '—' : '—'), h('div', { className: 'hbb-txt' }, t.text)),
        ),
        digest?.sections?.notices?.length ? secTitle('待回应通知') : null,
        (digest?.sections?.notices || []).map((n, i) =>
          h('div', { className: 'hbb-item', key: n.id || i }, h('div', { className: 'hbb-txt' }, n.text, h('small', {}, n.room || '')), h('span', { className: 'hbb-tag' }, '通知')),
        ),
        digest?.sections?.pendingRuns?.length
          ? secTitle(`工作台任务（${digest?.counts?.pendingRuns ?? digest.sections.pendingRuns.length}）`)
          : null,
        (digest?.sections?.pendingRuns || []).map((r) => {
          const when = r.at ? new Date(r.at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
          const hot = r.status === '待你确认' || r.status === '受阻' || r.status === '可重试';
          return h(
            'div',
            { className: 'hbb-item', key: r.id },
            h('div', { className: 'hbb-txt' }, r.title, h('small', {}, [r.conversation, when].filter(Boolean).join(' · '))),
            h('span', { className: 'hbb-tag ' + (hot ? 'red' : 'blue') }, r.status || '进行中'),
          );
        }),
        digest?.sections?.alerts?.length ? secTitle('服务异常') : null,
        (digest?.sections?.alerts || []).map((a) => h('div', { className: 'hbb-item', key: a.unit }, h('div', { className: 'hbb-txt' }, a.unit), h('span', { className: 'hbb-tag red' }, '异常'))),
        digest?.sections?.upcoming?.length ? secTitle('未来三天') : null,
        (digest?.sections?.upcoming || []).map((m) => h('div', { className: 'hbb-item', key: m.id }, h('div', { className: 'hbb-when' }, m.day), h('div', { className: 'hbb-txt' }, m.title, h('small', {}, m.time)))),
        status?.devices?.length
          ? null
          : h('div', { style: { marginTop: 12, color: '#9aa8bb', fontSize: 10.5 } }, '提示：当前没有在线的小歪设备。插 USB 或用同一 WiFi 配网后，这里会自动出现。'),
      );
    }

    /* ---------------- 小歪对话 ---------------- */
    function ChatTab({ onSpeakText }) {
      useStyle();
      const [items, setItems] = useState([]);
      const [text, setText] = useState('');
      const [busy, setBusy] = useState(false);
      const [alsoSpeak, setAlsoSpeak] = useState(false);
      const [err, setErr] = useState('');
      const boxRef = useRef(null);
      const load = useCallback(() => {
        api('GET', '/chat')
          .then((d) => setItems(d.items || []))
          .catch((e) => setErr(e.message));
      }, []);
      useEffect(() => {
        load();
      }, [load]);
      useEffect(() => {
        if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
      }, [items, busy]);
      const send = async () => {
        const q = text.trim();
        if (!q || busy) return;
        setBusy(true);
        setErr('');
        setItems((prev) => [...prev, { role: 'user', content: q }]);
        setText('');
        try {
          const r = await api('POST', '/chat', { text: q, speak: alsoSpeak });
          setItems((prev) => [...prev, { role: 'assistant', content: r.answer || '' }]);
        } catch (e) {
          setErr(e.message);
        } finally {
          setBusy(false);
        }
      };
      return h(
        'div',
        { className: 'hbb-chat' },
        h(
          'div',
          { className: 'hbb-msgs', ref: boxRef },
          items.length === 0 ? empty('问它「今天要干什么」，或让它「先把最要紧的念一遍」') : null,
          items.map((m, i) => h('div', { key: i, className: 'hbb-msg' + (m.role === 'user' ? ' me' : '') }, m.content)),
          busy ? h('div', { className: 'hbb-msg' }, '…') : null,
        ),
        err ? h('div', { className: 'hbb-err' }, err) : null,
        h(
          'div',
          { className: 'hbb-input' },
          h('textarea', { value: text, placeholder: '今天要干什么？', onChange: (e) => setText(e.target.value), onKeyDown: (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send(); } }),
          h(
            'div',
            { className: 'hbb-row', style: { margin: '6px 0 0' } },
            h('label', { className: 'hbb-switch' }, h('input', { type: 'checkbox', checked: alsoSpeak, onChange: (e) => setAlsoSpeak(e.target.checked) }), '回答顺便念出来'),
            h('button', { className: 'hbb-btn primary', style: { marginLeft: 'auto' }, disabled: busy, onClick: send }, busy ? '思考中…' : '发送'),
          ),
        ),
      );
    }

    /* ---------------- 待办 ---------------- */
    function TodoTab({ onChange }) {
      useStyle();
      const [items, setItems] = useState([]);
      const [text, setText] = useState('');
      const [due, setDue] = useState('');
      const [err, setErr] = useState('');
      const load = useCallback(() => {
        api('GET', '/todos').then((d) => setItems(d.items || [])).catch((e) => setErr(e.message));
      }, []);
      useEffect(() => {
        load();
      }, [load]);
      const add = async () => {
        if (!text.trim()) return;
        try {
          await api('POST', '/todos', { text: text.trim(), due });
          setText('');
          setDue('');
          load();
          onChange?.();
        } catch (e) {
          setErr(e.message);
        }
      };
      const toggle = async (t) => {
        await api('PATCH', '/todos/' + encodeURIComponent(t.id), { done: !t.done }).catch((e) => setErr(e.message));
        load();
        onChange?.();
      };
      const remove = async (t) => {
        await api('DELETE', '/todos/' + encodeURIComponent(t.id)).catch((e) => setErr(e.message));
        load();
        onChange?.();
      };
      return h(
        'div',
        {},
        err ? h('div', { className: 'hbb-err' }, err) : null,
        h(
          'div',
          { className: 'hbb-grid' },
          h('div', { className: 'hbb-field' }, h('label', {}, '新待办'), h('input', { type: 'text', value: text, placeholder: '要做什么', onChange: (e) => setText(e.target.value) })),
          h('div', { className: 'hbb-field' }, h('label', {}, '截止（可空）'), h('input', { type: 'text', value: due, placeholder: '2026-10-21 18:00', onChange: (e) => setDue(e.target.value) })),
        ),
        h('div', { className: 'hbb-row', style: { marginTop: 0 } }, h('button', { className: 'hbb-btn primary', onClick: add }, '加入今日')),
        items.length === 0 ? empty('还没有手动待办。这里加的事会进今日汇报的待办清单。') : null,
        items.map((t) =>
          h(
            'div',
            { className: 'hbb-todo' + (t.done ? ' done' : ''), key: t.id },
            h('button', { className: 'hbb-chk' + (t.done ? ' on' : ''), onClick: () => toggle(t) }, t.done ? '✓' : ''),
            h('div', { className: 'hbb-txt' }, t.text, t.due ? h('small', {}, '截止 ' + String(t.due).replace('T', ' ')) : null),
            h('button', { className: 'hbb-del', onClick: () => remove(t), title: '删除' }, '×'),
          ),
        ),
      );
    }

    /* ---------------- 设置 ---------------- */
    function SettingsTab({ config, onSaved }) {
      useStyle();
      const [form, setForm] = useState(() => JSON.parse(JSON.stringify(config || {})));
      const [saving, setSaving] = useState(false);
      const [msg, setMsg] = useState('');
      const [err, setErr] = useState('');
      useEffect(() => {
        if (config) setForm(JSON.parse(JSON.stringify(config)));
      }, [config]);
      const set = (pathStr, value) => {
        setForm((prev) => {
          const next = JSON.parse(JSON.stringify(prev || {}));
          const parts = pathStr.split('.');
          let cur = next;
          for (let i = 0; i < parts.length - 1; i += 1) {
            cur[parts[i]] = cur[parts[i]] || {};
            cur = cur[parts[i]];
          }
          cur[parts[parts.length - 1]] = value;
          return next;
        });
      };
      const save = async () => {
        setSaving(true);
        setErr('');
        setMsg('');
        try {
          const r = await api('PUT', '/config', { config: form });
          setMsg('已保存，下一轮调度立即生效');
          onSaved?.(r.config);
        } catch (e) {
          setErr(e.message);
        } finally {
          setSaving(false);
        }
      };
      if (!form) return empty('配置加载中…');
      const field = (label, pathStr, opts = {}) =>
        h(
          'div',
          { className: 'hbb-field', key: pathStr },
          h('label', {}, label),
          h('input', {
            type: opts.type || 'text',
            value: form?.[pathStr.split('.')[0]]?.[pathStr.split('.')[1]] ?? '',
            placeholder: opts.placeholder || '',
            onChange: (e) => set(pathStr, opts.type === 'number' ? Number(e.target.value) : e.target.value),
          }),
          opts.hint ? h('div', { className: 'hint' }, opts.hint) : null,
        );
      return h(
        'div',
        {},
        msg ? h('div', { className: 'hbb-ok' }, msg) : null,
        err ? h('div', { className: 'hbb-err' }, err) : null,
        secTitle('小歪服务'),
        field('服务地址', 'xiaowai.baseUrl', { hint: 'deskbot-server 在本机的地址，默认 127.0.0.1:9000' }),
        field('设备 ID（留空自动选在线设备）', 'xiaowai.deviceId'),
        secTitle('会前提醒'),
        h(
          'div',
          { className: 'hbb-field' },
          h('label', {}, '开关'),
          h(
            'label',
            { className: 'hbb-switch' },
            h('input', { type: 'checkbox', checked: Boolean(form.reminder?.enabled), onChange: (e) => set('reminder.enabled', e.target.checked) }),
            '会议开始前主动播报',
          ),
        ),
        h(
          'div',
          { className: 'hbb-grid' },
          field('提前几分钟', 'reminder.minutesBefore', { type: 'number', hint: '默认 10 分钟' }),
          field('每小时上限', 'reminder.maxPerHour', { type: 'number' }),
          field('静默开始', 'reminder.quietStart', { type: 'time' }),
          field('静默结束', 'reminder.quietEnd', { type: 'time' }),
        ),
        h(
          'label',
          { className: 'hbb-switch' },
          h('input', { type: 'checkbox', checked: form.reminder?.speakOnReminder !== false, onChange: (e) => set('reminder.speakOnReminder', e.target.checked) }),
          '提醒时直接播报（关掉只记录不念）',
        ),
        secTitle('数据源'),
        h(
          'div',
          { className: 'hbb-grid' },
          ...[
            ['会议日程', 'sources.meetings'],
            ['Bot 群聊', 'sources.botChats'],
            ['工作台任务', 'sources.workbench'],
            ['系统告警', 'sources.alerts'],
            ['手动待办', 'sources.manual'],
          ].map(([label, key]) =>
            h(
              'label',
              { className: 'hbb-switch', key },
              h('input', {
                type: 'checkbox',
                checked: Boolean(form?.sources?.[key.split('.')[1]]),
                onChange: (e) => set(key, e.target.checked),
              }),
              label,
            ),
          ),
        ),
        secTitle('语音链路（ASR / TTS）'),
        h(
          'div',
          { className: 'hbb-field' },
          h('label', {}, '密钥来源'),
          h(
            'select',
            { value: form.speech?.source || 'env', onChange: (e) => set('speech.source', e.target.value) },
            h('option', { value: 'env' }, '沿用小歪服务 .env（推荐）'),
            h('option', { value: 'manual' }, '在本页单独填写'),
          ),
        ),
        field('TTS 模型', 'speech.ttsModel'),
        field('音色', 'speech.voice'),
        form.speech?.source === 'manual' ? field('API Key', 'speech.apiKey', { type: 'password' }) : null,
        secTitle('汇报稿生成模型'),
        h(
          'div',
          { className: 'hbb-field' },
          h(
            'label',
            { className: 'hbb-switch' },
            h('input', { type: 'checkbox', checked: Boolean(form.digestModel?.polish), onChange: (e) => set('digestModel.polish', e.target.checked) }),
            '用模型润色成口语（关掉用固定模板，最省）',
          ),
        ),
        h(
          'div',
          { className: 'hbb-grid' },
          field('Base URL', 'digestModel.baseUrl'),
          field('模型', 'digestModel.model'),
        ),
        h(
          'div',
          { className: 'hbb-field' },
          h('label', {}, '密钥来源'),
          h(
            'select',
            { value: form.digestModel?.source || 'env', onChange: (e) => set('digestModel.source', e.target.value) },
            h('option', { value: 'env' }, '沿用小歪服务 .env'),
            h('option', { value: 'manual' }, '在本页单独填写'),
          ),
        ),
        form.digestModel?.source === 'manual' ? field('API Key', 'digestModel.apiKey', { type: 'password' }) : null,
        h('div', { className: 'hbb-row' }, h('button', { className: 'hbb-btn primary', disabled: saving, onClick: save }, saving ? '保存中…' : '保存设置')),
      );
    }

    /* ---------------- 主面板 ---------------- */
    function Panel() {
      useStyle();
      const [tab, setTab] = useState('today');
      const [status, setStatus] = useState(null);
      const [digest, setDigest] = useState(null);
      const [config, setConfig] = useState(null);
      const [busy, setBusy] = useState(false);
      const [toast, setToast] = useState('');

      const loadStatus = useCallback(async () => {
        try {
          const s = await api('GET', '/status');
          setStatus(s);
          setConfig(s.config);
        } catch (e) {
          setStatus({ error: e.message });
        }
      }, []);
      const loadDigest = useCallback(async () => {
        try {
          const d = await api('GET', '/digest');
          setDigest(d);
        } catch (e) {
          setDigest({ headline: '汇总失败', speakText: e.message, counts: {} });
        }
      }, []);
      useEffect(() => {
        loadStatus();
        loadDigest();
        const t = setInterval(loadStatus, 60000);
        return () => clearInterval(t);
      }, [loadStatus, loadDigest]);

      const doSpeak = async () => {
        setBusy(true);
        setToast('');
        try {
          const r = await api('POST', '/speak', {});
          setToast(r.ok ? `已送到小歪（${r.result?.deviceId || ''}）` : `播报失败：${r.result?.error || 'unknown'}`);
        } catch (e) {
          setToast('播报失败：' + e.message);
        } finally {
          setBusy(false);
        }
      };

      const svcOk = status?.service?.ok;
      const onlineDevices = (status?.devices || []).filter((d) => d.online);

      return h(
        'div',
        { className: 'hbb' },
        h(
          'div',
          { className: 'hbb-head' },
          h(Icon, { size: 20 }),
          h('h1', {}, '汇报助手'),
          h(Dot, { state: status?.error ? 'err' : svcOk ? 'ok' : '' }),
          h('span', { style: { fontSize: 10, color: '#8494a8' } }, status?.error ? '服务异常' : svcOk ? `小歪在线 · 设备 ${onlineDevices.length}` : '服务离线'),
        ),
        h(
          'div',
          { className: 'hbb-tabs' },
          ...[
            ['today', '今日汇报'],
            ['chat', '问小歪'],
            ['todo', '待办'],
            ['settings', '设置'],
          ].map(([k, label]) => h('button', { key: k, className: 'hbb-tab' + (tab === k ? ' on' : ''), onClick: () => setTab(k) }, label)),
        ),
        toast ? h('div', { className: 'hbb-ok', style: { margin: '8px 14px 0' } }, toast) : null,
        h(
          'div',
          { className: 'hbb-body', style: tab === 'chat' ? { display: 'flex', flexDirection: 'column' } : undefined },
          tab === 'today' ? h(TodayTab, { digest, status, busy, onRefresh: loadDigest, onSpeak: doSpeak }) : null,
          tab === 'chat' ? h(ChatTab, {}) : null,
          tab === 'todo' ? h(TodoTab, { onChange: () => { loadDigest(); loadStatus(); } }) : null,
          tab === 'settings' ? h(SettingsTab, { config, onSaved: (c) => { setConfig(c); loadStatus(); } }) : null,
        ),
      );
    }

    /* ── DSH 工作台 × 小歪 R6 控制台：同源嵌入 ────────────────────────
     * 控制台由 nginx 以 /xw/ 前缀反代到 127.0.0.1:5050（同源 + SAMEORIGIN），
     * 因此这里可以直接读 iframe 文档，把 DSH 的浅/深色底色同步注入。
     * （DSH 导航栏联动在 Main 面板层，见上方 HB-NAVLINK-20261021。）
     * ------------------------------------------------------------------ */
    const CONSOLE_PATH = '/xw/home';

    let PLUGIN_CTX = null;
    const getLayout = () => {
      try {
        if (!PLUGIN_CTX) return null;
        // 兜底：apply 阶段若 layout 注入尚未就绪，从 ctx.get 再取一次。
        return PLUGIN_CTX.layout || (PLUGIN_CTX.get && PLUGIN_CTX.get('layout')) || null;
      } catch (e) {
        return null;
      }
    };

    /* ── HB-NAVLINK-20261021：DSH 左侧导航栏联动 ────────────────────────
     * 进入「汇报助手」主面板（cap:huibao-bot）时收起 DSH 左侧导航栏，
     * 把整块宽度让给本页；离开本面板（点「工作台」/其它能力包/会话/团队视图）时展开。
     *
     * 与 2026-10-21 18:10 workbench-bot「Bot 协作」同一套做法：
     *   1) 挂在 main 面板组件的挂载/卸载副作用上，不挂在子视图上——
     *      控制台 ⇄ 汇报面板之间切换属于同一面板，不再把导航栏抖开（旧实现在 ConsoleView 上）；
     *   2) 只用「当前真实状态」判断，绝不盲目 toggle：已收起就不再收，未收起就不再展开；
     *   3) 只展开「本次由本插件收起」的侧栏，用户手动收起的偏好不被覆盖；
     *   4) 只操作宽屏（>=1024px）——窄屏由框架自动收起成抽屉，这里不插手。
     * ------------------------------------------------------------------ */
    const HOST_SIDEBAR_MIN_WIDTH = 1024;
    /** 框架根节点（AppFrame）带着 data-sidebar-collapsed 属性。 */
    const hostSidebarFrame = () => {
      try {
        const anchor = document.querySelector('[data-rightbar-col]');
        return anchor ? anchor.parentElement : null;
      } catch (e) {
        return null;
      }
    };
    let hostSidebarOwned = false;
    function setHostSidebarCollapsed(collapsed) {
      const layout = getLayout();
      const frame = hostSidebarFrame();
      if (!layout || typeof layout.toggleSidebar !== 'function' || !frame) return false;
      const isCollapsed = frame.hasAttribute('data-sidebar-collapsed');
      if (collapsed) {
        if (isCollapsed || window.innerWidth < HOST_SIDEBAR_MIN_WIDTH) return false;
        try {
          layout.toggleSidebar();
          hostSidebarOwned = true;
          return true;
        } catch (e) {
          return false;
        }
      }
      if (!hostSidebarOwned || !isCollapsed) {
        hostSidebarOwned = false;
        return false;
      }
      hostSidebarOwned = false;
      try {
        layout.toggleSidebar();
        return true;
      } catch (e) {
        return false;
      }
    }

    /** 跟随 DSH 主题（body[data-ds-dark-theme]）实时切换。 */
    function useDshDark() {
      const read = () => Boolean(document.body && document.body.hasAttribute('data-ds-dark-theme'));
      const [dark, setDark] = useState(read);
      useEffect(() => {
        const body = document.body;
        if (!body) return undefined;
        const obs = new MutationObserver(() => setDark(read()));
        obs.observe(body, { attributes: true, attributeFilter: ['data-ds-dark-theme'] });
        setDark(read());
        return () => obs.disconnect();
      }, []);
      return dark;
    }

    const EMBED_CSS = {
      // 浅色：与 DSH 画布同色（--dsw-alias-bg-base = #fff），顶栏/侧栏贴 DSH 侧栏灰。
      light: [
        'html,body{background:#ffffff !important;background-image:none !important;color-scheme:light}',
        'body{--bg:#ffffff}',
        '.topbar,.side{background:#f9fafb !important}',
      ].join('\n'),
      // 深色：底色 #151517 与 DSH 深色画布一致，主要面板/文字跟着翻转，避免白底刺眼。
      dark: [
        'html,body{background:#151517 !important;background-image:none !important;color-scheme:dark}',
        'body{--bg:#151517;--panel:#1c1c1f;--panel2:#232327;--soft:#232327;--ink:#e9eaec;',
        '--dim:#a4a8ae;--faint:#7d818a;--line:#3b3c41;--accent-soft:#1f3a2c;',
        '--shadow:0 8px 24px rgba(0,0,0,.45);--shadow-sm:0 2px 7px rgba(0,0,0,.38)}',
        '.topbar,.side{background:#1c1c1f !important;border-color:#3b3c41 !important}',
        '.topbar .tb-name,.topbar .tb-sub,.side .brand b,.side .brand small{color:#e9eaec}',
        '.navi .lbl{color:#7d818a}',
      ].join('\n'),
    };

    function paintConsole(doc, dark) {
      if (!doc || !doc.documentElement) return;
      const root = doc.documentElement;
      root.setAttribute('data-dsh-embed', dark ? 'dark' : 'light');
      let style = doc.getElementById('dsh-embed-theme');
      if (!style) {
        style = doc.createElement('style');
        style.id = 'dsh-embed-theme';
        (doc.head || root).appendChild(style);
      }
      const css = dark ? EMBED_CSS.dark : EMBED_CSS.light;
      if (style.textContent !== css) style.textContent = css;
    }

    function ConsoleView({ onPanel }) {
      useStyle();
      const frameRef = useRef(null);
      const dark = useDshDark();

      const sync = useCallback(() => {
        const frame = frameRef.current;
        if (!frame) return;
        try {
          paintConsole(frame.contentDocument, dark);
        } catch (e) {
          /* iframe 尚未就绪时忽略，load 后会重试 */
        }
      }, [dark]);

      useEffect(() => {
        sync();
      }, [sync]);

      return h(
        'div',
        { className: 'hbb-embed' },
        h('iframe', {
          ref: frameRef,
          className: 'hbb-embed-frame',
          src: CONSOLE_PATH,
          title: '小歪 R6 控制台',
          allow: 'camera; microphone; autoplay; fullscreen; clipboard-read; clipboard-write',
          onLoad: sync,
        }),
        onPanel
          ? h('button', { className: 'hbb-fab', type: 'button', onClick: onPanel, title: '切换到汇报助手面板' }, '汇报面板')
          : null,
      );
    }

    /** 默认展示小歪控制台；右下角小胶囊可切回原来的汇报助手面板。 */
    function EmbeddedRoot() {
      const [view, setView] = useState('console');
      if (view === 'panel') {
        return h(
          'div',
          { className: 'hbb hbb-panelmode' },
          h(Panel, {}),
          h('button', { className: 'hbb-fab', type: 'button', onClick: () => setView('console'), title: '回到小歪控制台' }, '控制台'),
        );
      }
      return h(ConsoleView, { onPanel: () => setView('panel') });
    }

    /** 主面板（本插件在工作台里被选中时挂载）：进入收起侧栏，离开恢复。
     *  放在 main 面板层而不是子视图层，控制台 ⇄ 汇报面板切换不影响侧栏。 */
    const Main = () => {
      useStyle();
      useEffect(() => {
        setHostSidebarCollapsed(true);
        return () => setHostSidebarCollapsed(false);
      }, []);
      return h(EmbeddedRoot, {});
    };

    /* ── 能力包契约（CAPABILITY-PACK-API v1.0）───────────────────────────────
     * 与「知识库 / 会议助手 / 政策解读 / 汇文 PPT」一致：
     *   1. sidebar.panellist 的 id 与 main 的 key 一律 `cap:<id>` 前缀，
     *      于是本能力包落在侧边栏「工作台」分组下（可展开/收起、可拖动排序），
     *      而不是作为顶级插件按钮出现在「插件」下方；
     *   2. entryKey 必须等于该 panellist id（宿主 MANIFEST 已如此声明），
     *      工作台首页卡片「打开」按 entryKey 切面板；
     *   3. 另注册 `cappacks:open-<id>` 事件桥，供工作台首页/子菜单派发打开。
     * ---------------------------------------------------------------------- */
    const CAP_ID = 'huibao-bot';
    const PANEL_ID = 'cap:' + CAP_ID;
    const OPEN_EVENTS = ['cappacks:open-huibao-bot', 'cappacks:open-huibao', 'huibao-bot:open'];

    /* 侧边栏清理：ds-bot 的「DS Bot」返回按钮（id bot-return）与知识库插件的
     * 「知识库」快捷入口（id knowledge-base）会堆在「设置」上方。这个场景不需要它们，
     * 用同 id + 更低 priority 的 list 条目遮蔽（list slot 单元格按 priority 升序取最低者渲染）。 */
    const HIDE_FOOTER_ACTIONS = ['bot-return', 'knowledge-base'];
    const Nothing = () => null;

    return {
      inject: ['slots', 'layout'],
      apply(ctx) {
        PLUGIN_CTX = ctx;
        const disposers = [];
        const slots = ctx.slots || (ctx.get && ctx.get('slots'));
        const layout = () => {
          try {
            return ctx.layout || (ctx.get && ctx.get('layout'));
          } catch {
            return null;
          }
        };
        if (!slots) return () => {};

        // 统一用 slots.inject 延迟注册：等待目标 slot 声明后再落登记（避免加载顺序问题）
        const reg = (slot, options, component) => {
          try {
            disposers.push(slots.inject(slot, () => slots.register({ name: slot, ...options }, component)));
          } catch (e) {
            console.error(`[huibao-bot] ${slot} 注册失败`, e);
          }
        };

        // 1. 能力包主面板（工作台选择 cap:huibao-bot 时渲染）
        reg('main', { key: PANEL_ID }, Main);

        // 2. 侧边栏「工作台」分组内的能力包行
        reg('sidebar.panellist', { id: PANEL_ID, label: '汇报助手', order: 24 }, Icon);

        // 3. 打开事件桥：工作台首页卡片「打开」/ 子菜单派发
        const onOpen = () => {
          const l = layout();
          try {
            if (l && typeof l.selectPanel === 'function') l.selectPanel(PANEL_ID);
          } catch {
            /* ignore */
          }
        };
        for (const name of OPEN_EVENTS) {
          try {
            window.addEventListener(name, onOpen);
            disposers.push(() => window.removeEventListener(name, onOpen));
          } catch {
            /* ignore */
          }
        }

        // 4. 遮蔽侧边栏底部多余的快捷入口（DS Bot / 知识库）：同 id + 更低 priority 者胜出
        for (const id of HIDE_FOOTER_ACTIONS) {
          reg('sidebar.footer.action', { id, priority: -1000, order: 0 }, Nothing);
        }

        return () => {
          for (const d of disposers.reverse()) {
            try {
              if (typeof d === 'function') d();
            } catch {
              /* ignore */
            }
          }
        };
      },
    };
  },
});

/* PATCH:V5-o2pack-panel */
/* 授权锁屏：内联自 license-panel.js（判 usable，零票轮询） */
/* license-panel.js —— DSH 插件授权锁屏（浏览器侧，零依赖，内联进插件 client.js）
 *
 * 设计约束（改这个文件前必须读）：
 *  1. 判「能不能用」只看 state.usable，**不是** state.ready。
 *     gate.state() 的 ready = !reason && !pending —— pool=0 时它依然是 true。
 *     用 ready 判会出现「界面显示已授权，一点就 403」（方案 §4.5 / 计划 F5）。
 *  2. 软门禁只允许调 /license/state（零票端点）。禁止在轮询里探业务接口 ——
 *     业务接口走 allow() 会消耗票，用户"只是开着页面"就会把票池吃空。
 *  3. 网络故障一律 fail-open：不锁屏、只提示并继续重试。
 *     平台停服/断网不该让客户以为授权丢了（T12）。
 *  4. 不 import 任何东西：由 license_build.py 直接把本文件拼进 client.js。
 *
 * 用法（插件 client.js 里）：
 *   mountLicenseGate({ pluginId: 'huiwen-ppt', prefix: '/api/huiwen-ppt' });
 */
(function (global) {
  'use strict';

  var REASONS = {
    offline_no_license: { t: '本机尚未激活', d: '导入离线激活包，或输入激活码后联网激活。', act: true },
    no_ticket: { t: '本地授权票已用尽', d: '联网自动补票，或导入新的离线激活包。', act: true },
    expired: { t: '授权已到期', d: '请联系服务商续期。', act: true },
    revoked: { t: '授权已停用', d: '请联系服务商处理。', act: false },
    pending: { t: '授权正在审核', d: '审核通过后会自动解锁，无需重复操作。', act: false },
    unknown_plugin: { t: '该功能当前不可用', d: '插件未在授权平台登记，请联系服务商。', act: false },
    clock_tamper: { t: '系统时间异常', d: '请校正系统时间后重试（检测到时钟回拨）。', act: false },
    mismatch: { t: '授权与本插件不匹配', d: '导入的包/激活码不属于本插件。', act: true },
    bad_signature: { t: '授权数据校验失败', d: '请重新导入官方激活包。', act: true },
    gate_init_failed: { t: '授权运行时初始化失败', d: '请联系服务商并附上日志。', act: false },
  };

  /* 纯函数：把 state 判成 { lock, title, desc, canAct }
   * 单独抽出来是为了能在 node 里测（见 test/panel_lock.mjs）——
   * 这条判定逻辑出错的代价是"全部客户被锁"或"全部客户免费用"，值得单独的回归测试。 */
  function licenseGateDecision(state) {
    if (!state || typeof state !== 'object') {
      // 拿不到状态（网络/未插桩）→ fail-open，不锁
      return { lock: false, title: '', desc: '', canAct: false, unknown: true };
    }
    if (state.usable === true) return { lock: false, title: '', desc: '', canAct: false };
    // 没有 reason 但不可用，最常见的就是"票池空了"：gate.state() 在 pool=0 时
    // reason 仍是 null（票是可再补的燃料，不是终态错误）。不兜这一层的话
    // 界面会显示"原因：未知"，现场既看不懂也没法自助（导包/联网补票就能好）。
    var r = state.reason || (state.pending ? 'pending' : (state.pool === 0 ? 'no_ticket' : ''));
    var m = REASONS[r] || { t: '授权不可用', d: '原因：' + (r || '未知') + '，请联系服务商。', act: false };
    return { lock: true, reason: r, title: m.t, desc: m.d, canAct: !!m.act };
  }

  var CSS_ID = 'dshlic-style';
  var CSS = '' +
    '.dshlic-mask{position:fixed;inset:0;z-index:2147483000;background:rgba(8,10,14,.72);' +
    'backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;font:14px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}' +
    '.dshlic-card{width:min(520px,92vw);background:#fff;color:#1b1f24;border-radius:14px;padding:26px 26px 20px;' +
    'box-shadow:0 24px 64px rgba(0,0,0,.36)}' +
    '.dshlic-card h3{margin:0 0 6px;font-size:18px;font-weight:600}' +
    '.dshlic-desc{color:#5b6470;margin:0 0 16px;font-size:13px}' +
    '.dshlic-mc{display:flex;align-items:center;gap:8px;background:#f4f6f8;border:1px solid #e3e7ec;' +
    'border-radius:8px;padding:8px 10px;font:12px/1.4 ui-monospace,Consolas,monospace;word-break:break-all}' +
    '.dshlic-mc b{font-weight:600;color:#0f62fe;cursor:pointer}' +
    '.dshlic-row{display:flex;gap:8px;margin-top:12px}' +
    '.dshlic-row input{flex:1;min-width:0;border:1px solid #ccd3db;border-radius:8px;padding:9px 11px;font:13px ui-monospace,monospace}' +
    '.dshlic-btn{border:0;border-radius:8px;padding:9px 16px;font-size:13px;cursor:pointer;background:#0f62fe;color:#fff}' +
    '.dshlic-btn[disabled]{opacity:.55;cursor:default}' +
    '.dshlic-btn.ghost{background:#eef1f5;color:#1b1f24}' +
    '.dshlic-drop{margin-top:14px;border:1.5px dashed #ccd3db;border-radius:10px;padding:18px;text-align:center;' +
    'color:#7b8694;font-size:13px;cursor:pointer;transition:.15s}' +
    '.dshlic-drop.hot{border-color:#0f62fe;background:#f2f7ff;color:#0f62fe}' +
    '.dshlic-msg{margin-top:12px;font-size:12.5px;min-height:18px}' +
    '.dshlic-msg.err{color:#c62f2f}.dshlic-msg.ok{color:#1a7f45}' +
    '.dshlic-foot{margin-top:14px;display:flex;justify-content:space-between;align-items:center;font-size:11.5px;color:#98a2ae}' +
    '.dshlic-foot a{color:#98a2ae;text-decoration:none}';

  function injectCss(doc) {
    if (doc.getElementById(CSS_ID)) return;
    var st = doc.createElement('style');
    st.id = CSS_ID;
    st.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(st);
  }

  function el(doc, tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* 挂载锁屏。返回 { refresh, destroy } —— 幂等，可重复调用。 */
  function mountLicenseGate(opts) {
    opts = opts || {};
    var win = opts.window || (typeof window !== 'undefined' ? window : null);
    if (!win) return { refresh: function () {}, destroy: function () {} };
    var doc = opts.document || win.document;
    var pluginId = String(opts.pluginId || '');
    var prefix = String(opts.prefix || ('/api/' + pluginId)).replace(/\/+$/, '');
    var base = prefix + '/license';
    var pollMs = Math.max(5000, Number(opts.pollMs || 15000));
    var onState = typeof opts.onState === 'function' ? opts.onState : null;
    var fetchImpl = opts.fetch || (win.fetch ? win.fetch.bind(win) : null);

    var mask = null, timer = null, destroyed = false, busy = false;

    function cleanup() {
      if (timer) { win.clearInterval(timer); timer = null; }
      if (mask && mask.parentNode) mask.parentNode.removeChild(mask);
      mask = null;
    }

    function msg(text, kind) {
      if (!mask) return;
      var m = mask.querySelector('.dshlic-msg');
      if (!m) return;
      m.textContent = text || '';
      m.className = 'dshlic-msg' + (kind ? ' ' + kind : '');
    }

    // ---- 唯一允许的自动请求：零票 state ----
    function getState() {
      if (!fetchImpl) return Promise.resolve(null);
      return fetchImpl(base + '/state', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ plugin_id: pluginId }) })
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; });   // 网络故障 → null → fail-open
    }

    function post(path, body, isBinary) {
      if (!fetchImpl) return Promise.reject(new Error('no fetch'));
      var init = { method: 'POST' };
      if (isBinary) {
        init.headers = { 'content-type': 'application/octet-stream' };
        init.body = body;
      } else {
        init.headers = { 'content-type': 'application/json' };
        init.body = JSON.stringify(body || {});
      }
      return fetchImpl(base + (path || ''), init).then(function (r) {
        return r.json().catch(function () { return { ok: false, error: 'bad_response' }; })
          .then(function (j) { return { status: r.status, body: j }; });
      });
    }

    function render(state) {
      var d = licenseGateDecision(state);
      if (onState) { try { onState(state, d); } catch (e) {} }
      if (!d.lock) { cleanup(); return; }
      if (mask) { // 已挂载 → 只更新文案
        var h = mask.querySelector('h3'); if (h) h.textContent = d.title;
        var p = mask.querySelector('.dshlic-desc'); if (p) p.textContent = d.desc;
        var act = mask.querySelector('.dshlic-row');
        if (act) act.style.display = d.canAct ? 'flex' : 'none';
        var drop = mask.querySelector('.dshlic-drop');
        if (drop) drop.style.display = d.canAct ? 'block' : 'none';
        return;
      }
      injectCss(doc);
      mask = el(doc, 'div', 'dshlic-mask');
      var card = el(doc, 'div', 'dshlic-card');
      card.appendChild(el(doc, 'h3', null, d.title));
      card.appendChild(el(doc, 'p', 'dshlic-desc', d.desc));

      // 机器码：现场报障的唯一凭据，必须可一键复制
      var mc = el(doc, 'div', 'dshlic-mc');
      mc.appendChild(el(doc, 'span', null, '机器码'));
      var code = el(doc, 'b', null, (state && state.machine_code) || '读取中…');
      code.title = '点击复制完整机器码';
      code.onclick = function () {
        var full = (state && state.hwid) || '';
        try {
          if (win.navigator && win.navigator.clipboard) win.navigator.clipboard.writeText(full);
          else {
            var ta = doc.createElement('textarea');
            ta.value = full; doc.body.appendChild(ta); ta.select(); doc.execCommand('copy'); doc.body.removeChild(ta);
          }
          msg('机器码已复制', 'ok');
        } catch (e) { msg('复制失败，请手动记录：' + full, 'err'); }
      };
      mc.appendChild(code);
      card.appendChild(mc);

      // 激活码
      var row = el(doc, 'div', 'dshlic-row');
      if (!d.canAct) row.style.display = 'none';
      var inp = el(doc, 'input');
      inp.placeholder = '输入激活码（lic_…）';
      inp.autocomplete = 'off';
      var btn = el(doc, 'button', 'dshlic-btn', '激活');
      btn.onclick = function () {
        var v = String(inp.value || '').trim();
        if (!v || busy) return;
        busy = true; btn.disabled = true; msg('正在激活…');
        post('/activate', { code: v }).then(function (r) {
          var st = (r.body && r.body.state) || r.body || {};
          if (r.body && r.body.ok) { msg('激活成功', 'ok'); render(st); }
          else msg('激活失败：' + ((st && st.reason) || (r.body && r.body.error) || '未知原因'), 'err');
        }).catch(function (e) { msg('激活请求失败：' + (e && e.message), 'err'); })
          .then(function () { busy = false; btn.disabled = false; });
      };
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') btn.click(); });
      row.appendChild(inp); row.appendChild(btn);
      card.appendChild(row);

      // 离线包拖拽
      var drop = el(doc, 'div', 'dshlic-drop', '把 .licpack 激活包拖到这里，或点击选择文件');
      if (!d.canAct) drop.style.display = 'none';
      var file = el(doc, 'input');
      file.type = 'file'; file.accept = '.licpack,application/octet-stream'; file.style.display = 'none';
      function doImport(f) {
        if (!f || busy) return;
        busy = true; msg('正在导入 ' + f.name + '…');
        var rd = new win.FileReader();
        rd.onload = function () {
          post('/import', rd.result, true).then(function (r) {
            var b = r.body || {};
            if (b.ok) {
              msg('导入成功：' + (b.via === 'suite' ? '套件证' : '插件证') + '，可用票 ' + (b.pool != null ? b.pool : '?'), 'ok');
              refresh();
            } else msg('导入失败：' + (b.message || b.error || '未知原因'), 'err');
          }).catch(function (e) { msg('导入请求失败：' + (e && e.message), 'err'); })
            .then(function () { busy = false; });
        };
        rd.readAsArrayBuffer(f);
      }
      drop.onclick = function () { file.click(); };
      file.onchange = function () { doImport(file.files && file.files[0]); };
      ['dragenter', 'dragover'].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add('hot'); });
      });
      ['dragleave', 'drop'].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove('hot'); });
      });
      drop.addEventListener('drop', function (e) {
        var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        doImport(f);
      });
      card.appendChild(drop);
      card.appendChild(file);

      card.appendChild(el(doc, 'div', 'dshlic-msg'));
      var foot = el(doc, 'div', 'dshlic-foot');
      foot.appendChild(el(doc, 'span', null, pluginId + ' · ' + ((state && state.server) || '')));
      var retry = el(doc, 'a', null, '刷新状态');
      retry.href = 'javascript:void(0)';
      retry.onclick = function () { refresh(); };
      foot.appendChild(retry);
      card.appendChild(foot);

      mask.appendChild(card);
      (doc.body || doc.documentElement).appendChild(mask);
    }

    function refresh() {
      if (destroyed) return Promise.resolve(null);
      return getState().then(function (st) { if (!destroyed) render(st); return st; });
    }

    function startPolling() {
      if (timer) return;
      timer = win.setInterval(function () {
        // 页面不可见时不轮询：既省请求，也避免后台标签页被打断后误判
        if (doc.visibilityState === 'hidden') return;
        refresh();
      }, pollMs);
    }

    startPolling();
    refresh();

    return {
      refresh: refresh,
      destroy: function () { destroyed = true; cleanup(); },
    };
  }

  var api = { mountLicenseGate: mountLicenseGate, licenseGateDecision: licenseGateDecision, REASONS: REASONS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (global) global.mountLicenseGate = mountLicenseGate, global.licenseGateDecision = licenseGateDecision;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));

try { if (typeof mountLicenseGate === 'function') mountLicenseGate({ pluginId: "huibao-bot", prefix: "/api/huibao-bot" }); } catch (e) {}
