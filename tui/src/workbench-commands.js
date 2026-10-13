// Workbench actions are independent of Ink so protocol and validation stay testable.
import { credentialValues, redactSettings, redactText, safeCommandEcho } from './sensitive-text.js';
import { runResearchCommand } from './research-commands.js';
import { runFeatureCommand } from './feature-commands.js';
import { runOnboardingCommand } from './onboarding.js';
import { runQueueCommand } from './queue-commands.js';
import { runSteerCommand } from './steer-commands.js';
import { runReviewCommand } from './review-commands.js';
import { runHistoryCommand } from './history-commands.js';
import { runOutputCommand } from './output-commands.js';
export { redactSettings, safeCommandEcho };
export const featureKeys = ['ui_animations', 'desktop_inspector', 'enable_crossref', 'git_archive',
  'mcp_enabled', 'plugins_enabled', 'default_offline', 'llm_enabled', 'memory_enabled', 'subagents_enabled',
  'hooks_enabled', 'browser_control_enabled', 'computer_control_enabled', 'experimental_message_queue', 'experimental_inflight_steering', 'experimental_evidence_reviews'];
const featureLabels = { ui_animations: '界面动画', desktop_inspector: '桌面工具面板',
  enable_crossref: 'Crossref 检索', git_archive: 'Git 归档', mcp_enabled: 'MCP 扩展',
  plugins_enabled: '插件系统', default_offline: '默认离线', llm_enabled: '模型调用', experimental_message_queue: '实验性下一轮队列' };

export function motionEnabled({ isTTY, settings = {}, env = process.env }) {
  return Boolean(isTTY) && settings.ui_animations !== false && !/^(1|true|yes|on)$/i.test(env.GEZHI_REDUCED_MOTION || '')
    && !Object.hasOwn(env, 'NO_COLOR') && String(env.TERM || '').toLowerCase() !== 'dumb';
}
export const panelVisible = (columns, enabled) => Boolean(enabled && columns >= 110);

export async function runWorkbenchCommand(name, args, context) {
  if (await runOutputCommand(name, args, context)) return true;
  if (await runHistoryCommand(name, args, context)) return true;
  if (await runReviewCommand(name, args, context)) return true;
  if (await runSteerCommand(name, args, context)) return true;
  if (await runOnboardingCommand(name, args, context)) return true;
  if (await runQueueCommand(name, args, context)) return true;
  if (await runFeatureCommand(name, args, context)) return true;
  if (await runResearchCommand(name, args, context)) return true;
  const { bridge, out } = context;
  const parts = (args || '').trim().split(/\s+/).filter(Boolean);
  const usage = text => { out(`用法：${text}`, 'yellow'); return true; };
  const call = async request => {
    try {
      const result = await bridge.call(request);
      if (result.ok === false) throw new Error(result.error || '操作失败');
      return result;
    } catch (error) { throw new Error(redactText(error.message || error, credentialValues(request))); }
  };
  const loopLine = loop => `${loop.loop_id} · ${loop.status} · ${loop.iteration || 0}/${loop.max_iterations || '—'} 轮 · ${loop.workflow_id || ''}${loop.task_id ? ` · ${loop.task_id}` : ''}`;

  switch (name) {
    case 'processes': case 'ps': {
      const result = await call({ cmd: 'workbench_status' });
      context.setWorkbench?.(result);
      out(`任务进程 ${(result.processes || []).length} · Loop ${(result.loops || []).length}`);
      for (const process of result.processes || []) out(`${process.task_id} · ${process.status} · ${process.workflow_id || process.workflow || 'research_pipeline'} · ${process.offline ? '离线' : '在线'} · ${process.topic || ''}`);
      for (const loop of result.loops || []) out(loopLine(loop));
      out('跟踪任务 /open <任务ID> · 暂停 /pause · 续跑 /resume · 取消 /cancel');
      return true;
    }
    case 'workflows': {
      const result = await call({ cmd: 'workflows' });
      for (const flow of result.workflows || []) {
        out(`${flow.id} · ${flow.title}${flow.requires_approval ? ' · 含人工确认' : ''}`);
        out(`  ${flow.description || ''}`);
        out(`  ${(flow.stages || []).map(stage => typeof stage === 'string' ? stage : stage.title || stage.name || stage.id).join(' → ')}`);
      }
      out('运行：/workflow <id> <研究主题> · 定期运行：/loop start <id> <秒> <轮数> <主题>');
      return true;
    }
    case 'workflow': {
      if (parts.length < 2) return usage('/workflow <id> <研究主题>');
      const result = await call({ cmd: 'workflow_run', workflow_id: parts[0], topic: parts.slice(1).join(' ') });
      context.openTask?.(result.task_id);
      out(`工作流已启动：${parts[0]} · ${result.task_id}`);
      return true;
    }
    case 'loop': {
      const action = parts[0] || 'list';
      const intervalLimit = bridge.connection?.().mode === 'remote' ? 604800 : 31536000;
      let request;
      if (action === 'list' && parts.length <= 1) request = { cmd: 'loop_list' };
      else if (['stop', 'resume'].includes(action) && parts.length === 2) request = { cmd: `loop_${action}`, loop_id: parts[1] };
      else if (action === 'start' && parts.length >= 5 && Number.isFinite(Number(parts[2])) && Number(parts[2]) >= 1 && Number(parts[2]) <= intervalLimit && Number.isSafeInteger(Number(parts[3])) && Number(parts[3]) >= 1 && Number(parts[3]) <= 100) {
        request = { cmd: 'loop_start', workflow_id: parts[1], interval_seconds: Number(parts[2]), max_iterations: Number(parts[3]), topic: parts.slice(4).join(' ') };
      } else return usage('/loop start <工作流> <间隔秒数> <最大轮数> <主题> | list | stop <id> | resume <id>');
      const result = await call(request);
      const loops = result.loops || (result.loop ? [result.loop] : []);
      loops.forEach(loop => out(loopLine(loop)));
      if (!loops.length) out(action === 'list' ? '暂无 Loop。' : 'Loop 操作已完成。');
      if (result.loop?.task_id && action === 'start') context.openTask?.(result.loop.task_id);
      out('Loop 到人工审批会等待确认；/processes 查看关联任务。');
      return true;
    }
    case 'session': {
      if (parts.length !== 1) return usage('/session <会话ID>');
      const result = await call({ cmd: 'session_snapshot', session_id: parts[0] });
      const snapshot = redactSettings(result.snapshot || {});
      context.setCurrentAgentSession?.(parts[0]);
      context.openSession?.(parts[0], snapshot);
      return true;
    }
    case 'rewind': {
      const sid = context.currentAgentSession;
      if (!sid) {
        out('当前没有选中的 Agent 会话：先 /sessions 查看档案，或 /agent 运行一个会话后再回滚。', 'yellow');
        return true;
      }
      let turns = 1;
      if (parts[0]) {
        const n = Number(parts[0]);
        if (!Number.isSafeInteger(n) || n < 1) return usage('/rewind [回滚轮数，正整数，默认 1]');
        turns = n;
      }
      const result = await call({ cmd: 'session_rewind', session_id: sid, turns });
      out(`✎ 已回滚 ${turns} 轮（保留 ${result.rewound_to ?? '—'} 条事件 · 移除 ${result.removed ?? '—'} 条）`, 'green');
      if (result.warning) out(redactText(result.warning), 'yellow');
      // transcript 重载：回滚后的最新快照重新打开，阅读器与会话文件保持一致
      const snap = await call({ cmd: 'session_snapshot', session_id: sid });
      context.openSession?.(sid, redactSettings(snap.snapshot || {}));
      return true;
    }
    case 'session-export': {
      if (!parts[0] || parts.length > 2 || !['markdown', 'json'].includes(parts[1] || 'markdown')) return usage('/session-export <会话ID> [markdown|json]');
      const result = await call({ cmd: 'session_export', session_id: parts[0], format: parts[1] || 'markdown' });
      const destination = typeof result.content === 'string'
        ? await context.saveExport(result, parts[0], parts[1] || 'markdown') : result.path;
      if (!destination) throw new Error('导出未返回文件');
      out(`会话已导出：${destination}`);
      return true;
    }
    case 'remote': {
      const action = parts[0] || 'status';
      if (!['start', 'status', 'stop'].includes(action) || parts.length > (action === 'start' ? 2 : 1)) return usage('/remote start [端口] | status | stop');
      const port = parts[1] ? Number(parts[1]) : 8765;
      if (!Number.isInteger(port) || port < 1 || port > 65535) return usage('/remote start [1–65535]');
      const result = await call({ cmd: `remote_${action}`, ...(action === 'start' ? { host: '0.0.0.0', port } : {}) });
      const remote = result.remote || result;
      out(remote.running ? '局域网远控运行中' : '局域网远控已停止');
      (remote.urls || []).forEach(url => out(`手机浏览器：${url}`));
      if (action === 'start' && remote.token) out(`连接令牌（仅分享给自己的设备）：${remote.token}`);
      return true;
    }
    case 'connect': {
      if (parts.length !== 2) return usage('/connect <http(s)://主机:端口> <令牌>');
      bridge.connect(parts[0], parts[1]);
      const result = await call({ cmd: 'workbench_status' });
      context.setWorkbench?.(result);
      await context.connectionChanged?.();
      out(`已连接远程工作台：${bridge.connection().url}`);
      out('远程支持工作流、Loop、任务控制、会话与审批；设置和扩展配置请在主机操作。');
      return true;
    }
    case 'disconnect':
      bridge.disconnect();
      await bridge.whenReady?.();
      await context.connectionChanged?.();
      out('已切回本机工作台。');
      return true;
    case 'panel':
      if (!['on', 'off'].includes(args)) return usage('/panel <on|off>（终端至少 110 列时显示）');
      context.setPanel?.(args === 'on');
      out(args === 'on' ? '工具面板已开启（终端至少 110 列时显示）' : '工具面板已收起');
      return true;
    case 'terminal':
      await context.openTerminal();
      out('已打开本机原生终端。');
      return true;
    case 'toggle': {
      if (parts.length !== 2 || !featureKeys.includes(parts[0]) || !['on', 'off'].includes(parts[1])) return usage(`/toggle <${featureKeys.join('|')}> <on|off>`);
      const patch = { [parts[0]]: parts[1] === 'on' };
      await call({ cmd: 'settings_save', settings: patch });
      context.onSettings?.(patch);
      if (parts[0] === 'plugins_enabled') await context.refreshPlugins?.();
      out(`${featureLabels[parts[0]]}已${parts[1] === 'on' ? '开启' : '关闭'}`);
      return true;
    }
    case 'settings': {
      const result = await call({ cmd: 'settings_get' });
      const settings = result.settings || {};
      context.onSettings?.(settings);
      const safe = redactSettings(settings);
      Object.entries(safe).forEach(([key, value]) => out(`${featureLabels[key] || key}：${typeof value === 'object' ? JSON.stringify(value) : value}`));
      out('功能开关：/toggle <setting> <on|off> · 模型配置：/model');
      return true;
    }
    case 'plugins': {
      let result;
      if (['enable', 'disable'].includes(parts[0]) && parts.length === 2) {
        result = await call({ cmd: 'plugin_toggle', name: parts[1], on: parts[0] === 'enable' });
        out(`插件 ${parts[1]} 已${parts[0] === 'enable' ? '启用' : '停用'}`);
      } else if (!parts.length || (parts.length === 1 && parts[0] === 'reload')) {
        result = await call({ cmd: 'plugins_summary', reload: parts[0] === 'reload' });
      } else return usage('/plugins [reload|enable <名称>|disable <名称>]');
      const summary = result.plugins || result.summary || {};
      (summary.names || []).forEach(plugin => out(`${plugin.enabled === false ? '○ 已停用' : '● 已启用'} ${plugin.name} ${plugin.version || ''} · ${plugin.description || ''}`));
      (summary.errors || []).forEach(error => out(`插件错误：${error}`, 'yellow'));
      await context.refreshPlugins?.();
      return true;
    }
    case 'model': {
      if (!parts.length) return false; // Existing endpoint/model discovery stays available.
      if (bridge.connection?.().mode === 'remote') { out('模型设置请在主机本机 TUI 或桌面操作，远程连接不发送密钥。', 'yellow'); return true; }
      const keys = { default: 'llm_model', fast: 'llm_model_fast', strong: 'llm_model_strong', endpoint: 'llm_base_url', key: 'llm_api_key' };
      let patch;
      if (parts[0] === 'clear-key' && parts.length === 1) patch = { llm_api_key: '' };
      else if (parts[0] === 'clear-endpoint' && parts.length === 1) patch = { llm_base_url: '' };
      else if (keys[parts[0]]) {
        if (parts.length !== 2) return usage('/model [default|fast|strong|endpoint|key] <值> | clear-key | clear-endpoint');
        patch = { [keys[parts[0]]]: parts[1] };
      } else patch = { llm_model: args };
      await call({ cmd: 'settings_save', settings: patch });
      context.onSettings?.(patch);
      out(parts[0] === 'key' ? '模型密钥已保存（隐藏）' : parts[0] === 'clear-key' ? '模型密钥已清除' : '模型配置已保存');
      return true;
    }
    default: return false;
  }
}
