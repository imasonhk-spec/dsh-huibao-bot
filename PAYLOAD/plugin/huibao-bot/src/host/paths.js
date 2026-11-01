/* PATCH:S2-paths —— 可移植路径探测（四级：env → $DSH_HOME 派生 → 包内 bundled/ → 旧绝对路径兜底）
 * 依据《DSH插件打包依赖治理方案 §4.2》；禁止在业务代码里再散落绝对路径字符串。 */
import nodeOs from 'node:os';
import nodePath from 'node:path';
import { existsSync as pathExists, realpathSync } from 'node:fs';
import { fileURLToPath as pathFileUrl } from 'node:url';

export const PKG_ROOT = nodePath.dirname(nodePath.dirname(pathFileUrl(import.meta.url)));

export function dshHome() {
  const h = process.env.DSH_HOME;
  if (typeof h === 'string' && h.trim().length > 0) return h.trim();
  return nodePath.join(nodeOs.homedir(), '.dsh');
}

/**
 * 四级探测：env（可多个，逗号分隔仅取第一个非空） → dshSub 派生 → bundled → legacy 列表
 * 全都不存在时返回 fallback（通常是 dshSub 派生结论，供安装器创建）。
 */
export function resolvePath({ envKeys = [], dshSub, bundled, legacy = [], fallback } = {}) {
  for (const k of envKeys) {
    const v = process.env[k];
    if (typeof v === 'string' && v.trim().length > 0) return v.trim();
  }
  const derived = dshSub ? nodePath.join(dshHome(), dshSub) : null;
  if (derived && pathExists(derived)) return derived;
  if (bundled) {
    const p = nodePath.join(PKG_ROOT, bundled);
    if (pathExists(p)) return p;
  }
  for (const p of (Array.isArray(legacy) ? legacy : [legacy])) {
    if (p && pathExists(p)) return p;
  }
  return fallback || derived || null;
}

export { pathExists, realpathSync };
