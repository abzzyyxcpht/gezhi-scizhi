// @ 文件补全 —— 数据逻辑（纯函数 + 目录列举缓存；只列直接子项，安全可控）
import { readdirSync, statSync } from "node:fs"
import { join } from "node:path"

const dirCache = new Map()
const DIR_CACHE_TTL_MS = 2000

/** 从草稿解析末尾的 @查询：无 @ 或 @ 后含空格（已闭合）返回 null */
export function parseMentionQuery(draft) {
  const m = draft.match(/(?:^|\s)@([^\s@]*)$/)
  return m ? m[1] : null
}

/** 目录列举（带 2s TTL 缓存，避免每次按键扫盘） */
export function listDirEntries(cwd) {
  const now = Date.now()
  const hit = dirCache.get(cwd)
  if (hit && now - hit.at < DIR_CACHE_TTL_MS) return hit.entries
  let entries = []
  try {
    entries = readdirSync(cwd, { withFileTypes: true })
      .filter((d) => !d.name.startsWith("."))
      .slice(0, 200)
      .map((d) => {
        let isDir = d.isDirectory()
        if (d.isSymbolicLink()) {
          try { isDir = statSync(join(cwd, d.name)).isDirectory() } catch { /* 断链符号链接按文件 */ }
        }
        return { name: d.name, isDir }
      })
  } catch { entries = [] }
  dirCache.set(cwd, { at: now, entries })
  return entries
}

/** 候选：目录前缀优先 + 名称子序列；目录带 / 后缀；上限 8 */
export function fileCandidates(query, entries = [], limit = 8) {
  const q = (query || "").toLowerCase()
  const named = entries.map((e) => ({ ...e, label: e.isDir ? `${e.name}/` : e.name }))
  const prefix = named.filter((e) => e.name.toLowerCase().startsWith(q))
  const fuzzy = named.filter((e) => !e.name.toLowerCase().startsWith(q) && q && [...q].every((ch) => e.name.toLowerCase().includes(ch)))
  return [...prefix, ...fuzzy].slice(0, limit)
}
