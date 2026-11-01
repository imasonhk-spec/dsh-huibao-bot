/**
 * huibao-bot 配置与本地状态存储
 *
 * 运行数据一律落在 $DSH_HOME/huibao-bot/，包目录保持只读（与汇文 PPT / 政策解读同一契约）。
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { resolvePath } from './paths.js'; /* PATCH:S2-paths */

export function dshHome() {
  const h = process.env.DSH_HOME;
  if (typeof h === 'string' && h.length > 0) return h;
  return path.join(os.homedir(), '.dsh');
}

export function dataRoot() {
  return path.join(dshHome(), 'huibao-bot');
}

/* PATCH:S2-paths —— 四级探测：HUIBAO_SERVICE_DIR → $DSH_HOME/xiaowai-service
 *   → 包内 bundled/xiaowai-service → 旧部署路径兜底 */
export const SERVICE_DIR = resolvePath({
  envKeys: ['HUIBAO_SERVICE_DIR'],
  dshSub: 'xiaowai-service',
  bundled: 'bundled/xiaowai-service',
  legacy: ['/home/lgsj/01-DSH/11-汇报机器人/longshu-xiaowai-v2-upstream-full-r6/service'],
  fallback: path.join(dshHome(), 'xiaowai-service'),
});

// 默认值刻意与 R6 出厂配置对齐：语音走 modelhub 网关，汇报稿走轻量模型。
export const DEFAULT_CONFIG = {
  xiaowai: {
    baseUrl: 'http://127.0.0.1:9000',
    serviceDir: SERVICE_DIR,
    deviceId: '',
    speakTimeoutMs: 90000,
  },
  speech: {
    provider: 'openai_compatible',
    asrBaseUrl: 'https://modelhub.lgdg.cc/aigateway/v1',
    asrModel: 'FunAudioLLM/Fun-ASR-Nano-2512',
    ttsBaseUrl: 'https://modelhub.lgdg.cc/aigateway/v1',
    ttsModel: 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice',
    voice: 'serena',
    apiKey: '',
    source: 'env',
  },
  digestModel: {
    provider: 'openai',
    baseUrl: 'https://tokens.store/v1',
    model: 'deepseek-v4.1-flash',
    apiKey: '',
    source: 'env',
    polish: true,
    timeoutMs: 30000,
  },
  reminder: {
    enabled: true,
    minutesBefore: 10,
    quietStart: '22:00',
    quietEnd: '08:00',
    maxPerHour: 4,
    speakOnReminder: true,
  },
  sources: {
    meetings: true,
    botChats: true,
    workbench: true,
    // 只把最近 N 小时内仍在活动的 run 当成「在跑的任务」；终态（delivered 等）一律不显示
    workbenchWindowHours: 24,
    alerts: true,
    manual: true,
    projects: false,
  },
  projects: [],
};

function clone(v) {
  return JSON.parse(JSON.stringify(v));
}

function deepMerge(base, patch) {
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) return patch === undefined ? base : patch;
  const out = Array.isArray(base) ? [...base] : { ...(base || {}) };
  for (const [k, v] of Object.entries(patch)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && base && typeof base[k] === 'object' && !Array.isArray(base[k])) {
      out[k] = deepMerge(base[k], v);
    } else {
      out[k] = v;
    }
  }
  return out;
}

export function ensureRoot() {
  const root = dataRoot();
  fs.mkdirSync(root, { recursive: true });
  return root;
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

function writeJson(file, value) {
  ensureRoot();
  const tmp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(value, null, 2), 'utf8');
  fs.renameSync(tmp, file);
}

export function configPath() {
  return path.join(dataRoot(), 'config.json');
}

export function loadConfig() {
  const stored = readJson(configPath(), null);
  const merged = deepMerge(clone(DEFAULT_CONFIG), stored || {});
  // 环境变量兜底：便于首次部署不改文件就能指到 service 目录
  if (!process.env.HUIBAO_SERVICE_DIR && merged.xiaowai && !merged.xiaowai.serviceDir) {
    merged.xiaowai.serviceDir = SERVICE_DIR;
  }
  return merged;
}

export function saveConfig(patch) {
  const next = deepMerge(loadConfig(), patch || {});
  writeJson(configPath(), next);
  return next;
}

export function statePath() {
  return path.join(dataRoot(), 'state.json');
}

const DEFAULT_STATE = { todos: [], reminderLog: [], speakLog: [], chat: [], version: 1 };

export function loadState() {
  const s = readJson(statePath(), null);
  const merged = { ...clone(DEFAULT_STATE), ...(s || {}) };
  for (const key of ['todos', 'reminderLog', 'speakLog', 'chat']) {
    if (!Array.isArray(merged[key])) merged[key] = [];
  }
  return merged;
}

export function saveState(next) {
  writeJson(statePath(), next);
  return next;
}

export function updateState(mutator) {
  const s = loadState();
  const out = mutator(s) || s;
  return saveState(out);
}

/** 读小歪 service 的 .env（密钥来源；避免在插件里二次维护凭据） */
export function readServiceEnv(serviceDir) {
  const out = {};
  try {
    const text = fs.readFileSync(path.join(serviceDir || SERVICE_DIR, '.env'), 'utf8');
    for (const line of text.split(/\r?\n/)) {
      const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/.exec(line);
      if (!m) continue;
      out[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  } catch {
    /* .env 不存在时返回空对象，由调用方降级 */
  }
  return out;
}

/** 小歪本机进程间凭证 data/.free_api_key（文件里是 key=value 两行） */
export function readLocalApiKey(serviceDir) {
  try {
    const text = fs.readFileSync(path.join(serviceDir || SERVICE_DIR, 'data', '.free_api_key'), 'utf8');
    for (const line of text.split(/\r?\n/)) {
      const m = /^\s*api_key\s*=\s*(.+)\s*$/.exec(line);
      if (m) return m[1].trim();
    }
  } catch {
    /* 未生成时由服务端首次启动写入 */
  }
  return '';
}

export function resolveSpeech(cfg = loadConfig()) {
  const env = readServiceEnv(cfg.xiaowai?.serviceDir);
  if (cfg.speech?.source === 'manual' && cfg.speech.apiKey) {
    return {
      asrBaseUrl: cfg.speech.asrBaseUrl,
      asrModel: cfg.speech.asrModel,
      ttsBaseUrl: cfg.speech.ttsBaseUrl,
      ttsModel: cfg.speech.ttsModel,
      voice: cfg.speech.voice,
      apiKey: cfg.speech.apiKey,
    };
  }
  return {
    asrBaseUrl: cfg.speech?.asrBaseUrl || 'https://modelhub.lgdg.cc/aigateway/v1',
    asrModel: env.ASR_MODEL || cfg.speech?.asrModel,
    ttsBaseUrl: cfg.speech?.ttsBaseUrl || 'https://modelhub.lgdg.cc/aigateway/v1',
    ttsModel: env.TTS_MODEL || cfg.speech?.ttsModel,
    voice: env.TTS_VOICE || cfg.speech?.voice || 'serena',
    apiKey: env.TTS_API_KEY || env.ASR_API_KEY || '',
  };
}

export function resolveDigestModel(cfg = loadConfig()) {
  const env = readServiceEnv(cfg.xiaowai?.serviceDir);
  if (cfg.digestModel?.source === 'manual' && cfg.digestModel.apiKey) {
    return { ...cfg.digestModel };
  }
  return {
    ...cfg.digestModel,
    baseUrl: env.LLM_BASE_URL ? env.LLM_BASE_URL.replace(/\/chat\/completions\/?$/, '') : cfg.digestModel.baseUrl,
    model: env.LLM_MODEL || cfg.digestModel.model,
    apiKey: env.LLM_API_KEY || cfg.digestModel.apiKey,
  };
}
