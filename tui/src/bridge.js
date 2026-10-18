// JSON Lines bridge with correlated requests and an authenticated LAN transport.
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

export const live = [];

export class Bridge {
  constructor(options = {}) {
    this.options = options;
    this.pending = new Map();
    this.serial = [];
    this.sequence = 0;
    this.localGeneration = 0;
    this.closed = false;
    this.remote = null;
    this.controllers = new Set();
    this.timeoutMs = options.timeoutMs ?? 300000;
    const remoteUrl = options.remoteUrl ?? process.env.GEZHI_REMOTE_URL;
    live.push(this);
    if (remoteUrl) this.connect(remoteUrl, options.token ?? process.env.GEZHI_REMOTE_TOKEN ?? '');
    else this._startLocal();
  }

  _startLocal() {
    if (this.proc) this._localFailed(new Error('桥连接已重启'));
    const generation = ++this.localGeneration;
    this.buf = '';
    this.ready = false;
    this.supportsIds = false;
    this.localError = null;
    this._readyPromise = new Promise((resolve, reject) => {
      this._readyResolve = resolve; this._readyReject = reject;
    });
    this._readyPromise.catch(() => {});
    const proc = (this.options.spawn || spawn)('python', ['-m', 'src.bridge'], {
      cwd: ROOT, env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });
    this.proc = proc;
    const current = () => this.proc === proc && this.localGeneration === generation
      && !this.closed && !this.localError;
    proc.stdout.setEncoding('utf-8');
    proc.stdout.on('data', (chunk) => {
      if (!current()) return;
      this.buf += chunk;
      let idx;
      while ((idx = this.buf.indexOf('\n')) >= 0) {
        const line = this.buf.slice(0, idx).trim();
        this.buf = this.buf.slice(idx + 1);
        if (!line) continue;
        let msg;
        try { msg = JSON.parse(line); } catch { continue; }
        if (msg && msg.ready) {
          this.ready = true;
          this._readyResolve(true);
          this._flush();
          continue;
        }
        if (msg?.request_id != null) {
          this.supportsIds = true;
          const id = String(msg.request_id);
          this.serial = this.serial.filter(entry => entry.id !== id);
          const entry = this.pending.get(id);
          if (entry) this._settle(entry, null, msg);
        } else {
          // Legacy requests remain serial; timed out slots consume their late reply.
          if (this.supportsIds) continue;
          const entry = this.serial.shift();
          if (entry && this.pending.has(entry.id)) this._settle(entry, null, msg);
        }
        this._flush();
      }
    });
    proc.stderr.on('data', () => { if (!current()) return; });
    proc.stdin.on?.('error', () => { if (current()) this._localFailed(new Error('桥连接已断开')); });
    proc.on('error', () => { if (current()) this._localFailed(new Error('无法启动 Python 引擎')); });
    proc.on('exit', code => { if (current()) this._localFailed(new Error(`Python 引擎已退出（${code ?? '关闭'}）`)); });
  }

  _localFailed(error) {
    this.localError = error;
    this.ready = false;
    this._readyReject?.(error);
    for (const entry of [...this.pending.values()]) this._settle(entry, error);
    this.serial = [];
  }

  _settle(entry, error, response) {
    if (this.pending.get(entry.id) !== entry) return;
    clearTimeout(entry.timer);
    this.pending.delete(entry.id);
    if (error) entry.reject(error); else entry.resolve(response);
  }

  _flush() {
    if (!this.ready || this.localError || this.closed || this.remote) return;
    if (!this.supportsIds && this.serial.some(entry => entry.sent)) return;
    for (const entry of this.serial) {
      if (entry.sent) continue;
      entry.sent = true;
      try { this.proc.stdin.write(JSON.stringify({ ...entry.request, request_id: entry.id }) + '\n'); }
      catch { this._localFailed(new Error('桥连接已断开')); return; }
      if (!this.supportsIds) break;
    }
  }

  whenReady() { return this.remote ? Promise.resolve(true) : this._readyPromise; }

  call(request, options = {}) {
    if (this.closed) return Promise.reject(new Error('桥连接已关闭'));
    if (this.remote) return this._remoteCall(request, options);
    if (this.localError) return Promise.reject(this.localError);
    return new Promise((resolve, reject) => {
      const id = String(++this.sequence);
      const entry = { id, request, resolve, reject, sent: false, generation: this.localGeneration };
      entry.timer = setTimeout(() => {
        if (entry.generation !== this.localGeneration || this.pending.get(id) !== entry) return;
        this._settle(entry, new Error('桥响应超时'));
        if (this.supportsIds || !entry.sent) this.serial = this.serial.filter(item => item !== entry);
        this._flush();
      }, options.timeoutMs ?? this.timeoutMs);
      this.pending.set(id, entry);
      this.serial.push(entry);
      this._flush();
    });
  }

  async _remoteCall(request, options) {
    const remote = this.remote;
    const controller = new AbortController();
    this.controllers.add(controller);
    const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? this.timeoutMs);
    const requireCurrent = () => {
      if (controller.signal.aborted || this.closed || this.remote !== remote) {
        throw new Error('远程请求已中断或超时');
      }
    };
    try {
      const response = await (this.options.fetch || fetch)(`${remote.url}/api/command`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${remote.token}` },
        // HTTP already associates each response; the phone endpoint uses a strict field allowlist.
        body: JSON.stringify(request),
        signal: controller.signal, redirect: 'error',
      });
      requireCurrent();
      if (!response.ok) throw new Error(`远程连接失败（HTTP ${response.status}）`);
      const result = await response.json();
      requireCurrent();
      if (result?.error) result.error = String(result.error).split(remote.token).join('[已隐藏]');
      return result;
    } catch (error) {
      if (controller.signal.aborted || this.closed || this.remote !== remote) throw new Error('远程请求已中断或超时');
      const status = String(error.message).match(/^远程连接失败（HTTP \d+）$/);
      if (String(error.message).includes('HTTP 403')) throw new Error('远程未授权或不支持此命令（HTTP 403）；设置、扩展配置请在主机操作');
      throw new Error(status ? status[0] : '远程连接失败，请检查地址、令牌与主机服务');
    } finally { clearTimeout(timer); this.controllers.delete(controller); }
  }

  connect(url, token) {
    let parsed;
    try { parsed = new URL(url); } catch { throw new Error('远程地址无效'); }
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash || !token) {
      throw new Error('远程地址须为 HTTP(S) 地址，令牌单独提供');
    }
    this._abortRemote();
    this.remote = { url: parsed.href.replace(/\/$/, ''), token };
    const switched = new Error('已切换远程连接，本机请求已失效');
    for (const entry of [...this.pending.values()]) this._settle(entry, switched);
    // Already-sent legacy work still owns its reply slot. Keep the local
    // engine alive, but never execute commands queued before switching.
    this.serial = this.supportsIds ? [] : this.serial.filter(entry => entry.sent);
    return this.connection();
  }

  disconnect() {
    this._abortRemote();
    this.remote = null;
    if (!this.proc || this.localError) this._startLocal();
    return this.connection();
  }

  _abortRemote() { for (const controller of this.controllers) controller.abort(); }
  connection() { return { mode: this.remote ? 'remote' : 'local', url: this.remote?.url || '' }; }
  redact(value) { return this.remote?.token ? String(value).split(this.remote.token).join('[已隐藏]') : String(value); }

  close() {
    if (this.closed) return;
    this.closed = true;
    this._abortRemote();
    this._localFailed(new Error('桥连接已关闭'));
    // EOF lets Python run its finally block (including remote server cleanup).
    // A stalled worker must still not keep an exited TUI alive indefinitely.
    if (this.proc?.stdin?.end) {
      const proc = this.proc;
      const deadline = setTimeout(() => proc.kill(), 1500);
      deadline.unref?.();
      proc.once('exit', () => clearTimeout(deadline));
      try { proc.stdin.end(); }
      catch { clearTimeout(deadline); proc.kill(); }
    } else this.proc?.kill();
    const index = live.indexOf(this);
    if (index >= 0) live.splice(index, 1);
  }
}
