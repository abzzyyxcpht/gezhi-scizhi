import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

/** Remote export content is copied locally; server-provided paths are never used. */
export async function saveRemoteExport(result, sessionId, format, directory = path.join(process.cwd(), 'outputs', 'remote-sessions')) {
  if (typeof result.content !== 'string') throw new Error('远程导出内容无效');
  if (Buffer.byteLength(result.content, 'utf8') > 8 * 1024 * 1024) throw new Error('会话记录较大，请在主机导出');
  const safeId = String(sessionId).replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 100) || 'session';
  const filename = `${safeId}-${randomUUID().slice(0, 8)}.${format === 'json' ? 'json' : 'md'}`;
  await mkdir(directory, { recursive: true });
  const destination = path.resolve(directory, filename);
  await writeFile(destination, result.content, { encoding: 'utf8', flag: 'wx' });
  return destination;
}
