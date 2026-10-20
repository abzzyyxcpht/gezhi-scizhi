# 2026-10-20 安全加固与工程收口

本轮针对一次只读代码审查暴露的问题做收尾：不重写架构，只把「声明的边界」在代码里兑现，并补上可复跑的验收。所有结论以仓库内测试与脚本输出为准，未宣称任何未经测量的性能收益。

## 权限与执行边界

- **单一执行收口**：`AgentRuntime._execute_call`（唯一 handler 入口）在进入工具前先过 `_agent_gate`；`deny` 直接写 `permission_denied` 并返回结构化错误，handler 不执行。`ask` 语义仍由调用方审批，批准后的回放不会再弹一次审批。
- **拒绝原因不再串味**：`_agent_gate(include_tier=False)`（tools_schema 枚举）不再写 `_denial_reasons`；递归到 `mcp.call` 时沿用同一开关，避免枚举污染后续真实拒绝的提示。
- **显示层脱敏**：`event_page` 返回的事件统一过 `workbench.redact`；会话 JSONL 与审批回放读取的原始参数不变，跨进程恢复仍能拿到真实参数。
- **日志 fail-closed**：新增 `SessionStore.read(strict=True)`，审批幂等、跨进程恢复、终止判定、崩溃对账 8 处改走严格读取；尾部半行有 3×5ms 重试，持续不完整或坏行直接抛错，不再把损坏日志当作「未审批 / 未结束」。
- **deny 名单先归一化再匹配**：去引号、折叠空白、按 `; && || | &` 拆段，递归展开 `cmd /c`、`powershell -c/-Command`；覆盖 `rm -r -f`、`rm --recursive --force`、`del /q /s`、`rd /s /q`、`Remove-Item -Recurse`、`-EncodedCommand` 等写法。docstring 明确：这是尽力而为的纵深防御，人工审批才是主控制。
- **路径校验对齐**：`tools_workspace` 拒绝 ADS/冒号、Windows 设备保留名（含 `CON.txt`、COM¹ 变体）、空路径段与控制字符，并保留 resolve + `is_relative_to` 的越界拒绝。
- **复现沙箱**：`reproduce_run` 的命令出现 shell 元字符、不在白名单或命中禁用词一律拒绝；`cwd` 必须解析后落在项目根（`ctx.project_root` / `params["project_root"]` / 进程 cwd）内。
- **MCP 子进程收口**：stdio server 改经 `process_owner.spawn_owned_argv` 启动（Windows 原子 Job Object、POSIX 独立进程组），`close()` 终止整棵进程树，`npx → node` 后代不再残留；子进程环境改为最小白名单 + `server.env`，父进程的 `GEZHI_*` 凭据不再外泄。
- **权限元数据修正**：`plugin.call` 归为「完全访问 · 代码 · 不可逆」（插件是导入即执行的任意 Python）；`mcp.describe` 的影响说明如实写出「会启动外部进程」。

## 执行计划与完成依据

- `_execute_call` 对没有 `ok` 字段的工具结果做归一化（含 `error` → `False`，否则 `True`），使 `stats.analyze` 一类旧工具也能成为执行计划的完成回执；失败、超时、跨回合回执仍被拒绝。
- 不追认升级前的历史回执（旧 journal 里的无 `ok` 结果不会被读时迁移），避免出现第三套成功口径。

## 工程与发布门禁

- 新增 `scripts/refresh_source_manifest.py`（`--check` 模式可用于 CI）；`run_ci.py` 首步改为校验公开源码清单，pytest 的 `--basetemp` 移到系统临时目录并在 `finally` 清理。
- `release_check.py` 从「路径存在」升级为「存在 + 非空 + JSON 可解析 + 版本与 `src/version.py` 一致」。
- `tests/test_release_check.py` 改为在 `tmp_path` 副本上操作，不再删除/写回真实仓库文件。
- 删除已无入口的 `src/ui/tui.py`（Textual 旧实现）并移除 `textual` 依赖；`brand.py` 的「省下 X 小时」改为只陈述可核验计数的流水线回执，与 README「没有虚构性能提升」一致。
- `.gitignore` 去掉孤立的 `100`；TUI 组件注释里不存在的 `docs/tui-design-spec.md` 改指 `DESIGN.md`。

## 思考强度三段回执

思考强度控件此前只保存偏好，界面上无法区分「我选了 high」与「这次请求真的带了 high」。本轮把两者拆开，并补齐第三段事实：

- `src/reasoning_receipt.py` 从 `model_protocols.build_request` 产出的**真实请求体**反查思考参数（覆盖 5 种协议的嵌套路径，如 `generationConfig.thinkingConfig.thinkingLevel`、`thinking.budget_tokens`），得出「已发送 / 以 token 预算表达 / 未附带 / 本地适配器未发送」四种状态；不按档位推断。
- `normalize_usage` 新增 `reasoning_reported`：供应商没报推理用量与供应商报了 `0` 是两个不同事实，此前都折叠成 `0`。
- `log_model` 新增 `reasoning` 字段写入 `model_calls.jsonl`；`usage_summary()["reasoning"]` 汇总回执，`format_usage_text()`、桥接 `usage` 回执与思考强度控件下方的一行说明共用同一份数据。
- 本地适配器拒绝档位时（例如 OpenAI 不接受 `max`）记为 `unsupported` 并附原因，不会静默降级。
- 桌面端按日志签名节流刷新该行，日志未变化时不重读、不重绘。

模型对话框选档时也会预览真正要发送的参数：catalog 的每个档位附带适配器产出的 wire 键值，界面显示「将发送 `reasoning_effort=high`」或「请求不附带思考参数」；本地不支持的档位直接显示原因，按钮禁用，而不是保存后再失败。

验收在 `tests/test_reasoning_receipt.py`（纯函数）、`tests/test_reasoning_trail.py`（对 `tests/fake_llm_server.py` 跑真实传输，核对请求体与回执一致）、`tests/test_reasoning_usage_surface.py`（`/usage` 文本与桥接）、`tests/test_control_trail_ui.py` 与 `tests/test_desktop_effort_trail.py`（界面只显示日志里的记录，不拿档位冒充证据）。

## 验证方式

```powershell
$env:PYTHONUTF8 = "1"
$env:GEZHI_DATA_DIR = Join-Path $env:TEMP ("gezhi-check-" + [guid]::NewGuid().ToString("N"))
python scripts/refresh_source_manifest.py
python scripts/verify_public_source.py
python scripts/run_ci.py
```

## 已知边界

- deny 名单仍是启发式：变量展开、命令编码、别名与自写脚本可绕过；主控制始终是人工审批。
- MCP `close()` 直接终止进程树，不再等待服务端优雅退出；需要优雅退出的 server 需后续增加可选等待参数。
- `process_owner` 的 POSIX argv 分支未在 Windows 开发机上实测，需 Linux CI 覆盖。
- 仓库内不含 `build/`、`data/` 等运行产物；安装包与原生窗口仍需在发布机单独验证。
