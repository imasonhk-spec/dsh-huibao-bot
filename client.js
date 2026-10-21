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
