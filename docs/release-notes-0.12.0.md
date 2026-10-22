# 格致 SciZhi 0.12.0 发布说明（2026-10-22）

版本号唯一来源为 `src/version.py`；`tui/package.json` 与 `tui/package-lock.json` 同步到同一版本，`scripts/release_check.py` 会把两端版本不一致判为硬失败。本版不新增框架依赖、不改配置文件格式、不迁移既有数据目录。

## 变更

### 思考强度：请求、实际发送、供应商回报

- 每次模型调用在 `data/logs/model_calls.jsonl` 记录 `reasoning` 回执。实际发送项由 `src/reasoning_receipt.py` 从 `model_protocols.build_request` 生成的**真实请求体**反查（覆盖嵌套路径与 token 预算形态），不按档位推断。
- `src/model_protocols.py:552` 记录 `reasoning_reported`：供应商没回报推理用量与供应商回报 `0` 是两个不同事实，不再都折叠成 `0`。
- 本地适配器拒绝某档位时记为 `unsupported` 并附原因，不静默降级；回执只含档位、是否发送、键名与 token 数，不含提示词或密钥。

### 确定性成果验收

- `src/outcome_checks.py` 只读任务记录里已存在的事实，逐项给五档状态：`satisfied` / `failed` / `stale` / `pending` / `unknown`；整体结论只有 `all_satisfied` / `needs_review` / `incomplete` 三档（`src/outcome_checks.py:238`），"部分通过"不会被说成完成。
- 材料 hash 与磁盘当前内容不一致判 `stale`；记录里没有该项事实判 `unknown`，不当作通过。
- 人工复核项存放在任务产物之外，只能由用户显式写入；入口为 `service.outcome_checks`（只读）与 `outcome_review`（记录复核），见 `src/service.py:477`。

### 权限、日志与沙箱加固

- 单一执行入口进入工具前先过 `_agent_gate`（`src/agent_runtime.py:1171`）；`deny` 直接写 `permission_denied` 并返回结构化错误，handler 不执行。
- `SessionStore.read(..., strict=True)`（`src/agent_runtime.py:171`）用于审批幂等、跨进程恢复与崩溃对账：尾部半行重试后仍不完整即报错，不再把损坏日志当作"未审批 / 未结束"。
- deny 名单先归一化再匹配（去引号、折叠空白、按 `; && || | &` 拆段、递归展开 `cmd /c` 与 `powershell -c`）；docstring 明确这是尽力而为的纵深防御，人工审批才是主控制。
- MCP stdio server 改经 `process_owner.spawn_owned_argv` 启动，`close()` 终止整棵进程树，子进程环境改为最小白名单，父进程凭据不外泄。
- 路径校验拒绝 ADS/冒号、Windows 设备保留名、控制字符与越界路径。

### 终态与支持票语义

- TUI 终态按 `done/completed`、`blocked`、`cancelled`、`failed`、`partial`、`error`、`interrupted` 分别映射符号，未收录状态用中性符号，不再一律打勾。
- 支持票只表示"已录入 / 受反驳"；`verified` 需要独立核验记录，前端不再宣称"已核验"（`src/research_world.py:35`）。

### 工程与发布门禁

- 新增 `scripts/refresh_source_manifest.py`：按 Git 已跟踪文件重建 `SOURCE_MANIFEST.json`，`--check` 只比对不写入。
- `scripts/run_ci.py` 首步改为公开源码清单校验，pytest 的 `--basetemp` 移出仓库并在 `finally` 清理。
- `scripts/release_check.py` 从"路径存在"升级为"存在 + 非空 + JSON 可解析 + 版本与 `src/version.py` 一致"。
- 删除无入口的 `src/ui/tui.py` 与 `textual` 依赖。

## 影响面

- 兼容：新增字段与命令，旧会话日志、旧桥接请求仍可用；缺失 `reasoning_reported` 的旧记录按"未回报"处理。
- 需要留意：本机若安装了依赖 `src.ui.tui` 或 `textual` 的本地扩展，升级后不再提供；TUI 版本号由 `package.json` 与 lock 文件共同校验。
- 文档：README 顶部更新为 0.12.0，0.11.0 的陈述保留为历史；本文件与 `docs/hardening-2026-10-20.md`、`docs/outcome-checks-2026-10-22.md` 已加入 `scripts/prepare_opensource.py` 的 `DOC_FILES`，随公开源码包发布。

## 验证方式

```powershell
$env:PYTHONUTF8 = "1"
$env:GEZHI_DATA_DIR = Join-Path $env:TEMP ("gezhi-check-" + [guid]::NewGuid().ToString("N"))
python -m pytest tests/test_outcome_checks.py tests/test_outcome_checks_integration.py tests/test_reasoning_receipt.py tests/test_reasoning_trail.py tests/test_reasoning_usage_surface.py tests/test_release_check.py -q
python scripts/release_check.py
npm.cmd --prefix tui test
python scripts/run_ci.py
```

- 公开源码清单：先把新增文件 `git add`，再运行 `python scripts/refresh_source_manifest.py` 重写清单。
- 安装包：`.\scripts\build_desktop.bat` 生成 `dist/SciZhi-Agent-Setup-0.12.0.exe`，同时产出 SHA-256 校验文件与冻结自检报告。

## 已知边界

- 桌面与 TUI 尚无验收面板，`outcome_checks` / `outcome_review` 目前只能经桥接命令使用。
- 验收项固定为四类产物与四类事实，不能由用户自定义；复核记录只有"通过 / 未记录"两态。
- deny 名单是启发式：变量展开、命令编码、别名与自写脚本仍可绕过。
- MCP `close()` 直接终止进程树，不再等待服务端优雅退出。
- `process_owner` 的 POSIX argv 分支未在 Windows 开发机实测，需 Linux CI 覆盖。
- 原生窗口、输入法、终端兼容性与安装包本身仍需在发布机核对；离线评测只验证流程与标记，不构成真实检索或性能结论。
