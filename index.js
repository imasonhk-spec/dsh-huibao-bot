/**
 * 汇报助手（@local/huibao-bot）—— Host 半
 *
 * 职责：
 *   1. /api/huibao-bot/*  真实后端 API（今日聚合、汇报稿、让设备念、待办、配置）；
 *   2. 认证门禁：与工作台同一 connection.requestRejection；
 *   3. 会前提醒调度（每 30s 检查一次，命中「会前 N 分钟」就播报）；
 *   4. 向工作台注册能力清单（侧边栏卡片）；
 *   5. 注册工具：huibao_daily_digest / huibao_today / huibao_speak / huibao_add_todo / huibao_device_status。
 *
 * 硬性约束（与汇文 PPT / 政策解读同一契约）：
 *   - ESM；不 import 任何 npm 依赖，只用 node: 内置模块；
 *   - 运行数据写 $DSH_HOME/huibao-bot/，包目录保持只读。
 */
import { createApi } from './src/host/api.js';
import { registerTools } from './src/host/tools.js';
import { startScheduler } from './src/host/scheduler.js';
import { dataRoot, loadConfig } from './src/host/config.js';

export const VERSION = '0.1.3';

const PREFIX = '/api/huibao-bot';

/** 能力包清单（工作台侧边栏卡片） */
export const MANIFEST = {
  id: 'huibao-bot',
  title: '汇报助手',
  description: '今日会议/通知/待办/逾期聚合成口头汇报，推给小歪念出来；会前可配置提醒。',
  entryKey: 'cap:huibao-bot',
  order: 24,
  icon: '📣',
  category: '协作效率',
  version: 'v' + VERSION,
  tags: ['今日议程', '语音播报', '会前提醒'],
  subs: ['今日汇报', '小歪播报'],
  openEvent: 'cappacks:open-huibao-bot',
  openEventAliases: ['cappacks:open-huibao-bot'],
};

let PACK_CONTEXT = null;

import { installLicense } from './license-http.mjs'; /* PATCH:V5-o2pack-import */
export const inject = ['webServer', 'tools'];

export function apply(ctx, config = {}) {
  const disposers = [];

  /* PATCH:V5-o2pack-apply */
  // 授权门禁：本插件自带授权运行时，不依赖工作台；apply 阶段绝不 throw
  const __lic = installLicense(ctx, { pluginId: 'huibao-bot', prefix: '/api/huibao-bot' });
  try { disposers.push(() => { try { __lic.gate.flush(); } catch {} }); } catch {}
  PACK_CONTEXT = ctx;
  disposers.push(() => {
    PACK_CONTEXT = null;
  });

  const log = (...args) => {
    try {
      console.log('[huibao-bot]', ...args);
    } catch {
      /* ignore */
    }
  };

  // 启动时确保数据目录存在，并把真实路径打出来，便于运维定位
  try {
    const root = dataRoot();
    log(`数据目录：${root}`);
    const cfg = loadConfig();
    log(`目标服务：${cfg?.xiaowai?.baseUrl || '(未配置)'}，会前提醒：${cfg?.reminder?.enabled ? '开' : '关'} ${cfg?.reminder?.minutesBefore || 0} 分钟`);
  } catch (error) {
    log('初始化提示失败：' + String(error?.message || error));
  }

  const api = createApi({ log });

  // 1. HTTP 路由（先过同一认证门禁）
  try {
    const ws = ctx.webServer || (ctx.get && ctx.get('webServer'));
    if (ws && typeof ws.register === 'function') {
      disposers.push(
        ws.register({
          kind: 'prefix',
          path: PREFIX,
          handler(req, res) {
            if (__lic.guard(req, res)) return; /* PATCH:V5-o2pack-guard */
            const url = new URL(req.url || '/', 'http://localhost');
            let connection;
            try {
              connection = PACK_CONTEXT && PACK_CONTEXT.get && PACK_CONTEXT.get('connection');
            } catch {
              connection = null;
            }
            if (connection && typeof connection.requestRejection === 'function') {
              let rejection;
              try {
                rejection = connection.requestRejection({ headers: req.headers || {} });
              } catch {
                rejection = 503;
              }
              if (rejection !== undefined) {
                const status = rejection === 401 ? 401 : rejection === 403 ? 403 : 503;
                res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
                res.end(JSON.stringify({ ok: false, code: status === 401 ? 'UNAUTHORIZED' : status === 403 ? 'FORBIDDEN' : 'AUTH_UNAVAILABLE', error: '请求未通过工作台认证门禁' }));
                return;
              }
            }
            api.handle(req, res, url.pathname, req.method).catch((error) => {
              log('处理失败：' + String(error?.stack || error));
              try {
                res.writeHead(500, { 'content-type': 'application/json; charset=utf-8' });
                res.end(JSON.stringify({ ok: false, error: String(error?.message || error) }));
              } catch {
                /* socket 可能已关闭 */
              }
            });
          },
        }),
      );
      log(`HTTP 路由已挂载：${PREFIX}`);
    } else {
      log('webServer 不可用，仅保留 UI 与工具');
    }
  } catch (error) {
    log('路由注册失败：' + String(error?.message || error));
  }

  // 2. 工具注册
  try {
    const disposeTools = registerTools(ctx);
    disposers.push(disposeTools);
    log('工具已注册：huibao_daily_digest / huibao_today / huibao_speak / huibao_add_todo / huibao_device_status');
  } catch (error) {
    log('工具注册失败：' + String(error?.message || error));
  }

  // 3. 会前提醒调度
  try {
    const stop = startScheduler(log);
    disposers.push(stop);
    log('会前提醒调度已启动（30s 一轮）');
  } catch (error) {
    log('调度启动失败：' + String(error?.message || error));
  }

  // 4. 向工作台注册能力清单（延迟重试，保证冷启动卡片必现）
  try {
    let wc = null;
    const attempt = () => {
      try {
        wc = ctx.get && ctx.get('workbenchCapabilities');
      } catch {
        wc = null;
      }
      if (wc && typeof wc.register === 'function') {
        try {
          wc.register(MANIFEST);
        } catch {
          return false;
        }
        disposers.push(() => {
          try {
            if (typeof wc.unregister === 'function') wc.unregister(MANIFEST.id);
          } catch {
            /* ignore */
          }
        });
        return true;
      }
      return false;
    };
    if (!attempt()) {
      let tries = 0;
      const timer = setInterval(() => {
        tries += 1;
        if (attempt() || tries >= 20) clearInterval(timer);
      }, 150);
      if (typeof timer.unref === 'function') timer.unref();
      disposers.push(() => clearInterval(timer));
    }
  } catch {
    /* 工作台框架未启用 */
  }

  return () => {
    for (const dispose of disposers.reverse()) {
      try {
        if (typeof dispose === 'function') dispose();
      } catch {
        /* ignore */
      }
    }
  };
}

export const name = 'huibao-bot';
