# 格致 SciZhi · 科研智能体工作台

**0.9.0 本地候选版**：整合 GUI/TUI 交互、可恢复草稿、会话内工具输出、实时 shell 与受控进程树。开源许可 Apache-2.0；公开仓库：[github.com/abzzyyxcpht/gezhi-scizhi](https://github.com/abzzyyxcpht/gezhi-scizhi)。

2026-10-14 开始聚焦 Agent 本体：后续开发优先处理智能体运行时、科研工作流、权限审批、证据链和双端 UI 设计系统；路演材料与生成器不再作为源码工作区的一部分维护。

2026-10-15 双端 UI 对齐：Flet 与 OpenTUI 使用同一份 `assets/ui-design.json` 外观契约。设置支持系统/浅色/深色主题、独立界面/代码/内置终端字号、代码行号和换行、显示密度与动效。窄窗导航使用抽屉；跨端修改外观会自动同步并保留草稿。设计规范见 [DESIGN.md](DESIGN.md)。体验新版请运行 `python scripts/gezhi.py desktop` 或 `npm --prefix tui start`；既有安装包尚未重新构建。

> 2026-10-12 仓库精简：历史迭代文档（docs/）、验收证据（test-output/、design-previews/）、旧交付物与构建归档已按需清理，下文沿革章节中的历史链接随之失效；功能以当前源码与 `dist/` 安装包为准。

2026-10-12 桌面源码精修：固定侧栏设置入口，居中限宽阅读区与底部输入／执行区，按需展开高级选项；设置支持中英文分类搜索、正文／代码字体与可逆字号缩放，完善深浅主题交互状态。Python 全量回归 1691 通过、2 跳过，Ruff 与 UI 静态校验通过；OpenTUI 全量 71 个测试命令通过，含门禁／回看锚定与夹具失败清理回归。

2026-10-10 对标顶级 Agent 收尾轮：TUI 完全切换到 OpenTUI 单引擎（Ink 遗留代码仅测试保留，便携包已剔除）；新增可复现研究包 `/package`（manifest + 产物 + 工作流 + 数据 SHA256）、科研权限三档门（只读/工作区写入/完全访问 × 引用/数据/代码，审批卡带目标/影响/可逆性预览）、深浅双主题对比度修复与桌面侧栏渲染卡点修复。[本轮验收](test-output/b2-gui-verification-20261010.md) · [对标矩阵](docs/全球Agent对标矩阵.md)。

2026-10-04 双模式源码升级：桌面侧栏和 TUI `Ctrl+G` 切换通用／科研，`/mode general|research` 保存偏好。工作用途与 Agent 角色独立持久化；切换保留草稿和已有会话身份。新增 revision 冲突保护、确定取消终态、写工具重复副作用保护，以及忙态／远程命令可用性。[操作说明与验收](docs/dual-mode-harness-2026-10-04.md) · [真实双端预览](design-previews/2026-10-04-dual-mode/index.html) · [严格对标与差距](docs/research/2026-10-04-strict-harness-audit.md)。请从当前源码启动；下列已有发行包尚未重新打包。

2026-10-03 源码交互精修：原创 TUI 书页欢迎画面与八档键盘思考选择器，桌面完整档位菜单、固定输入/执行区、动态深色主题与窄终端阅读适配。[使用与验证](docs/agent-experience-polish-2026-10-03.md) · [原生截图与交互回放](design-previews/2026-10-03-agent-polish/index.html)。从源码启动体验；下列既有安装包和归档尚不包含本轮更新。

同日继续参考 ZCode / DeepSeek Harness 源码升级：补齐命令描述检索、阶段模型选择与目标强度、执行活动/子任务树、子会话审批、跨进程配置保存和桥恢复代次。[综合升级指南](docs/comprehensive-upgrade-2026-10-03.md) · [新版交互预览](design-previews/2026-10-03-comprehensive-upgrade/index.html)。

[Windows 安装包](dist/SciZhi-Agent-Setup-0.9.0.exe) · [安装包校验](dist/SciZhi-Agent-Setup-0.9.0.exe.sha256)

2026-10-02 文档与验证修订：统一从项目根目录启动，提供隔离研究数据的验证步骤，修正 Windows 句柄测试的首次初始化基线；运行代码和安装器未变，原始 20260928 源码包保留。

[使用指南](docs/harness-product-0.8.0.md) · [发布说明](docs/release-notes-0.8.0.md) · [原版验收](docs/release-0.8.0-verification.json) · [本轮收尾记录](docs/release-0.8.0-final-2026-10-02.md) · [交互预览](design-previews/tui-interaction-2026-09-28/index.html)

- **操作体验**：多行／完整字素编辑、分段粘贴、按会话和项目保存草稿、关闭保护与冲突比较。
- **视觉反馈**：GUI 思考强度分段选择与局部过渡，TUI 八帧启动采样及保存成功反馈，可关闭动画。
- **运行性能**：长结果只存一份可读归档，按字节读取／搜索；实时 shell 输出、停止按钮及 Windows 原子 Job 进程管理。
- **科研能力**：文献与证据账本、反驳材料对照、引用／统计工具、后台复核、可审计恢复。队列／运行中补充／后台复核三个实验开关默认关闭。

当前为本地候选版：安装器未签名，真实供应商与领域效果、跨平台／IME／辅助功能及自动更新未完成验收。既有0.7.0/0.6.0发行物保留；下方较早阶段记录不代表本次验收口径。

> 格物致知，智启科研——把事务交给智能体，把思考还给科研人。
> 中国国际大学生创新大赛（2026）产业赛道参赛作品 · 命题企业：中国联通浙江省分公司

项目与会话工作台本轮更新：目录项目、文件预览和附件、连续会话与归档、分叉与侧聊、Git 提交推送、定时研究、待办进度和科研交接包。Logo 与桌面/手机图标已更新，面板采用文字页签和紧凑列表。[操作说明与验证](docs/project-sessions-2026-09-19.md) · [品牌资产](docs/brand-identity.md)

## 产品形态（双端）

| 形态 | 技术栈 | 启动方式 |
|---|---|---|
| 桌面版（原生窗口） | Python + Flet（Flutter 渲染），PyInstaller 打包，NSIS 安装包 | 安装 `dist/SciZhi-Agent-Setup-0.9.0.exe`，或源码运行 `python scripts/gezhi.py` |
| 终端版（Node TUI） | Node.js + OpenTUI（React for CLI），经 stdio 桥调用 Python 引擎，默认 REPL 命令工作台 | 在项目根目录运行 `npm.cmd --prefix tui ci`，再运行 `npm.cmd --prefix tui start` |

两端共用同一套 Python 智能体引擎（`src/`），桌面版进程内直连，TUI 经 `src/bridge.py`（JSON Lines 协议）调用。

桌面版提供中性深浅双主题与蓝色操作色：左侧保留导航、独立滚动的最近研究和固定设置入口；中央阅读区居中且限制行宽，输入、附件和执行控件停靠底部。宽屏按需显示研究详情和侧边讨论；高级执行选项折叠展示。设置分类可用中英文搜索，正文使用带中文回退的系统字体，代码使用独立等宽字体，并支持字号、密度和动效偏好。

本轮对标研究与可追溯来源见 `docs/research/2026-09-16-harness-benchmark.md`。原生窗口截图可通过 `python scripts/capture_desktop.py` 在独立临时数据目录中复现。

## 科研记录与分类设置（2026-09-19）

左侧新增「研究记录」，连接研究问题、可证伪假设、支持/反驳证据、实验方法、实际结果与失败记录。Agent 可通过受审批的笔记工具读写，同一记录可由桌面和 TUI 管理并导出。设置整理为 17 类，补齐长期记忆、快捷键、命令模板、运行时钩子、Subagent 并行控制与 MCP 管理，沿用现有配色与布局。

MCP 新增 **2025 Streamable HTTP**，与 stdio 并存；浏览器、电脑控制可绑定实际 MCP 工具。手机端新增自适应会话继续、停止和关联任务入口。运行时进一步修复中断恢复、并行工具观察持久化、压缩归档和预算计量；工具失败不能被一句成功声明覆盖。

启动方式不变，源码版重启即可使用。模型接入、命令示例、支持范围和验收方法见 [升级说明](docs/workspace-upgrade-2026-09-19.md)，工程取舍见 [本轮调研](docs/research/2026-09-19-workspace-decisions.md)。

## 科研专属工作台（2026-09-17）

桌面右上角提供命令、科研工作流、任务进程、Loop、会话、手机远控和本机终端入口。宽屏在右侧打开工具面板，窄窗口使用弹出面板；`Ctrl+K` 打开命令入口。设置页新增真实生效的模型增强、离线示例、Git归档、MCP、插件、右栏与动效开关，插件可逐项启停和重载。模型设置支持端点、密钥、默认/分阶段模型与连接检测。

| 操作 | 桌面入口 | TUI 命令 |
| --- | --- | --- |
| 科研模板 | 右上角「科研工作流」 | `/workflows`、`/workflow <模板ID> <课题>` |
| 任务状态 | 「任务进程」面板 | `/processes`、`/open <任务ID>` |
| 循环研究 | 「Loop」面板，指定间隔与最多轮数 | `/loop start <模板ID> <间隔秒> <轮数> <课题>` |
| 循环控制 | 列表中的停止/恢复 | `/loop list`、`/loop stop <ID>`、`/loop resume <ID>` |
| 会话导出 | 「会话与导出」→ Markdown/JSON | `/session-export <会话ID> [markdown\|json]` |
| 手机远控 | 「手机远控」→ 开启局域网控制 | `/remote start [端口]`、`/remote status`、`/remote stop` |
| 本机终端 | 右上角终端按钮 | `/terminal` |
| 功能开关 | 设置页，保存后生效 | `/toggle <设置名> <on\|off>` |
| 插件管理 | 设置页逐项启停与重载 | `/plugins enable\|disable <名称>`、`/plugins reload` |

模板包括 `research_pipeline`（完整综述）、`literature_scan`（文献速览与引用清单）、`evidence_review`（人工复核与证据抽取）。新工作台命令默认使用明确标注的离线示例；真实检索请关闭面板的离线开关，或 `/toggle default_offline off`。Loop 每轮完成后再计时，不重叠执行，人工审批不会自动放行；宿主工作台必须保持运行，重启后需要手动恢复。

手机与电脑连同一局域网，在桌面开启远控后，用手机打开显示的局域网地址并输入口令。也可单独保持服务运行：

```powershell
python scripts/gezhi.py serve --host 0.0.0.0 --port 8765
```

远程 TUI 可使用 `/connect <地址> <口令>`，或启动前设置 `GEZHI_REMOTE_URL` 和 `GEZHI_REMOTE_TOKEN`；`/disconnect` 返回本机。远端修改模型与插件设置应在主机操作。终端按钮打开的是本机原生 shell，并非嵌入式 PTY；「任务进程」显示科研引擎管理的任务，不能任意控制系统进程。

工程依据见 `docs/research/2026-09-16-workbench-capabilities.md`；实测性能、方法和原始数据见 `docs/research/performance-2026-09-17-cache-comparison.md`。本机 100 会话列表 p95 从 65.148 ms 降至 4.248 ms；这不是与上游产品的同机对照实验。

工作台合同与性能验证的隔离命令见 [0.8.0 验证步骤](docs/harness-product-0.8.0.md#隔离验证)。验证报告应显式指定输出目录，避免覆盖历史验收文件。

## 命令工作台（REPL）

Agent 会话会持续观察状态：执行期间约 1.5 秒刷新，终态约 5 秒检查其他客户端的变更，读取失败约 3 秒重试；同 ID 继续或回滚会重新观察。局域网连接可使用编号审批及无参数 `/approve`、`/deny`。Esc 在回看时先回到底部，随后优先停止当前活动 Agent。

桌面「智能体」工具栏新增「对话记录」（每页 40 条，可查看完整已保存对话）与「回滚上一轮」。回滚保留至少一轮对话，只撤回会话记录，**不会撤销工具已经写入的文件、导出或外部操作**；运行和等待子任务期间拒绝回滚。

终端版对标 Claude Code / Kimi CLI 的交互范式：常驻输入框，斜杠命令与自然语言混排，执行日志实时滚动；支持 **↑↓ 历史召回**、**Tab 命令补全**、**斜杠命令补全面板**（输入 `/` 即列出候选与说明，↑↓ 选择、Tab/⏎ 采纳）、**行内编辑**（←→ 移动光标，Ctrl+A/E 行首行尾，Home/End 行首行尾，Ctrl+U/K/W 删至行首·行尾·删词，Ctrl+L 清屏）、**Esc 中断运行中的任务**、**Ctrl+C 双击退出**（首次只提示）。转写区支持 **PgUp/PgDn 回看历史输出**（Esc 回到底部）；终端行高自适应（80×24 下自动收起 Seal 与步骤链，保输入框可见）。到人工确认关口会弹出**编号选择菜单**（1 确认放行 / 2 剔除末位 / 3 展开池子 / 4 就地暂停），工具审批关口同样支持编号选择（1 批准 / 2 拒绝 / 3 看参数，`/approve` 无参即处理当前待审批调用），输入数字回车即执行；状态行常驻显示模型与累计 tokens。`/help` 一屏速览；**直接输入研究方向回车即开新任务**；`/menu` 可切回传统菜单模式（双向互切）。

| 类别 | 命令 |
|---|---|
| 会话 | `/help` `/menu` `/clear` `/exit` `/theme` |
| 任务 | `/new <方向> [关键词]` `/tasks` `/open <ID\|序号>` `/status` `/nodes` |
| 运行控制 | `/pause` `/resume` `/confirm` `/cancel` `/rerun` `/delete` |
| 干预与产物 | `/pool [N]` `/drop <序号>` `/digest` `/outline` `/cites` `/export` |
| 功能直达 | `/stats <数据文件>` `/style <gbt7714\|apa>` `/pdf <路径> [最大字符数]` |
| 系统 | `/usage` `/doctor` `/model [模型名]` `/settings` |
| 扩展生态 | `/init` `/context` `/skills` `/skill` `/plugins` `/mcp` `/git`（插件命令动态并入，如 `/bib` `/freq`） |
| 科研特性 | `/board` `/graph` `/read <面板>` `/ledger` |

## 科研特性工作台（看板 · 阅读器 · 时间账）

代码 Agent 观测代码仓库，格致观测**文献证据池与科研过程本身**——这一层是面向科研场景的原创设计：

| 能力 | 入口 | 说明 |
|---|---|---|
| 科研看板 | `/board` 或 `/read board` | 年份分布（领域冷热）、相关度分布（筛选质量）、递归收敛曲线（是否挖到底）、元数据覆盖率与**池健康度 0–100**、来源 Top、研究热词 Top 10（与 `/freq` 同口径） |
| 证据图谱（双端） | TUI：`/graph`、`/read graph`；桌面：成果页「证据图谱」页签 | 把"证据账本"变成图：**锚定率**（池里有几篇真被框架引用）、**孤儿文献**（进了池却未进任何章节：漏引还是该剔除）、**缺证据章节**（写到这里会无话可说）、防幻觉校验砍掉的引用数；外加**池内引用网络 PNG**（节点=相关度排名、边=池内互引关系，随导出带走）。大池只画相关度 Top 30，**图内副标题与桌面卡片说明都如实标注池的真实规模**；边密集时自动降透明度防"毛球" |
| 研究时间账 | `/ledger` | 跨任务累计：任务数、文献量、递归轮次与省下的事务时间（篇×6min + 轮×25min，与计划书"按周算→按小时算"同口径） |
| 全屏阅读器 | `/read digest\|outline\|cites\|board\|graph\|ledger` | 报告级浏览：标题栏 + **行号 gutter** + 进度条，↑↓ 逐行、←→ 十行、PgUp/PgDn 翻页、**`/` 搜关键词 + `n` 跳下一个命中**、`q` 返回 |
| 启动横幅与状态行 | 自动 | 首屏显示版本/流水线/入口；状态行常驻 `模式 · 技能 n/m · 插件 n · GEZHI ✔ · v0.3.0`；任务区带进度条 `████░░░░ 3/5 · 60%` |

## PDF 全文证据链

用户上传的 PDF 从"不支持的二进制"变为可核验的全文材料（`src/pdf_text.py`：pypdf / PyPDF2 优先，标准库 zlib 兜底解析 FlateDecode 内容流，零硬依赖）：

| 入口 | 行为 |
|---|---|
| 桌面附件 | 项目工作室选 PDF →「加入对话」；预览显示前 2000 字符与页数、提取方式 |
| `/pdf <路径> [最大字符数]` | 双端 REPL 命令：全文文本 + 页数/方式/SHA256 头，输出经脱敏 |
| 文献池 | `local_docs` 参数或 `ingest_local_docs` 工作流动作入池，条目带 `full_text` |
| 抽取 | `material_scope=fulltext`，出处按全文逐字核对，摘要稿标注全文字符数 |

扫描件、加密 PDF、损坏文件**明确报错不静默**；本地材料不参与检索评分（`score=None`，审计器如实上报"缺相关性分数"）；题名/作者/年份取 PDF 元数据，缺失留空不编造。交付说明见 [docs/pdf-evidence-chain-2026-09-24.md](docs/pdf-evidence-chain-2026-09-24.md)。

## 扩展生态（对标 Codex / Claude Code / Kimi CLI 的生态位）

| 能力 | 说明 | 入口 |
|---|---|---|
| 插件系统 | `plugins/` 一个 Python 文件即插件：注册 REPL 命令 + 工作流动作，导入失败不炸主程序 | `/plugins`；内置 `/bib`（文献池→BibTeX，兼容 LaTeX/Zotero）、`/freq`（术语词频） |
| Skill 技能包 | `skills/<名>/SKILL.md`（frontmatter + 正文），启用后正文注入系统提示词，无 LLM 时静默待命 | `/skills`、`/skill <名> <on\|off>`；内置学术写作规范、批判性阅读 |
| GEZHI.md 研究上下文 | 项目根目录的研究偏好声明（对标 AGENTS.md / CLAUDE.md 惯例），正文注入系统提示词，术语表优先级最高 | `/init` 生成模板、`/context` 查看状态 |
| MCP 工具生态 | stdio / 2025 Streamable HTTP 客户端，`data/mcp_servers.json` 配置；启用且 autoload 的服务发现工具并注册工作流动作 | `/mcp-config` 管理；`/mcp` 诊断；动作名 `mcp_<服务器>_<工具>` |
| 研究档案库 | `/export` 自动把产物 git commit 进 `data/research_repo`（Gezhi 身份署名）——研究过程有 diff、可回滚 | `/git log`、`/git status` |

## 功能总览（对应赛题五大核心功能）

| 功能 | 模块 | 状态 |
|---|---|---|
| F1 文献猎手：自动化检索与递归筛选 | `src/agents/literature.py` | ✔ **OpenAlex + CrossRef 双源并行检索**（并发召回、DOI 去重、单源失败降级，设置页可关）+ 余弦×BM25 融合评分（可插拔 Embedding 混合升级）+ 漏斗口径指标 + LLM 语义终审（可否决）+ 引用滚雪球递归 + 收敛判断 + 轮次级断点 |
| F2 综述搭子：信息抽取与综述框架 | `src/agents/extraction.py` | ✔ 结构化抽取（LLM 语义升级 + 规则兜底）+ 摘要整理稿 + 综述大纲（plan→act→reflect，支撑文献防幻觉校验）+ **缺证据驱动补检**（框架缺证据章节 → 补充检索入池 → 框架重写，单次上限） |
| F3 数据速览：实验数据统计可视化 | `src/agents/stats.py` | ✔ 描述统计 + IQR 异常值 + 相关性 + 图表建议与渲染 |
| F4 格式管家：参考文献格式化 | `src/citation/gbt7714.py` | ✔ GB/T 7714—2015 / APA 7 一键切换 + 元数据校对 |
| F5 暂停键：断点续跑与人工干预 | `src/orchestrator/engine.py` | ✔ 节点级 Checkpoint + 暂停/续跑 + 版本化干预 + HITL 审批 |
| 专项智能体编制表 | `src/agents/specs.py` + `specialists.py` | ✔ 9 个可独立验收智能体（OpenCode named agents + DeepSeek Harness 进程内 spawn/fan-out） |

## 任务控制（全生命周期）

引擎为每个任务提供完整生命周期管理，两端 UI 均可操作（终端版即 REPL 运行控制命令）：

| 操作 | 行为 |
|---|---|
| ⏸ 暂停 | 在文献递归的轮次间隙挂起，中间态落盘，可随时续跑 |
| ▶ 续跑 / 人工确认 | 从断点继续；审批节点视为确认后推进 |
| × 取消 | 在最近的节点边界终止（挂起状态即时生效）；产物保留备查 |
| ↻ 整体重跑 | 保留研究方向参数，清空产物从第一节点重新执行 |
| 删除 | 移除任务与断点存档（运行中的任务先取消再删，存档不会复活） |

监控页实时显示：节点步骤条、完成进度（n/N 节点 + 进度条）、累计耗时、彩色事件日志。

## 智能体工作台与权限面板（对标 Claude Code 的工具审批）

- **终端版**：`/plan` `/agent` `/spawn <智能体> <请求>` `/review` `/agents` `/approve` `/deny` `/sessions`
- **桌面版**：「智能体」页——选择专项智能体 → 一句话请求 → 规划/执行；工具触发权限门时弹出审批卡（批准 / 批准并本会话记住 / 拒绝）
- 权限策略（OpenCode 风格）：只读工具默认放行，写入/网络默认 ask；每个专项智能体另有工具白名单与权限覆盖；**策略可持久化**——桌面「智能体」页按工具编辑 allow/ask/deny 保存至 `settings.tool_permissions`（支持通配符如 `research.*`），即时生效且跨会话保留
- 子智能体（DeepSeek Harness）：`subagent.spawn` 进程内派发，`subagent.fanout` 并行文献子任务（在线时每路**真实双源检索** + 子会话 JSONL 审计 + 单路失败降级），子会话对父会话可见
- 底座韧性（对标顶级 harness）：模型驱动会话支持**上下文压缩**（达软阈值先摘要折叠而非终止）、**工具失败自愈**（错误回填模型修复，2 轮上限）、**单步并行只读工具**（≤4 并发、结果保序）、**中断对账恢复**（崩溃后按 JSONL 日志对账续跑）

## 用量统计（对标 /cost）

- 终端版 REPL：`/usage`；桌面版：设置页 →「用量统计」
- 聚合 `data/logs/model_calls.jsonl` 与 `tool_calls.jsonl`：调用次数、tokens（输入/输出）、平均延迟、失败数、按节点分布、最近调用

## AIGC 内容标识（合规）

依据《人工智能生成合成内容标识办法》，AI 参与生成的产物均带显式标识：

- 摘要整理稿 / 综述框架正文末尾带标识区：生成服务、生成方式（LLM/规则引擎）、生成时间、任务标识、提示词版本
- 导出目录自动生成 `manifest.md`：逐文件说明 AI 参与度与溯源信息（审计/合规材料）
- 参考文献 / 文献池 CSV 为确定性引擎排版，manifest 中明确标注"否（确定性排版）"

## RAG 三级检索漏斗（lexical + 语义向量 + LLM 终审）

文献相关性漏斗（`src/ranking.py` + `src/embedding.py`），每级通过数进事件流与产物 `funnel` 字段，可展示、可回放：

1. **召回**：OpenAlex + CrossRef 双源并行检索 + 引用滚雪球递归扩展；
2. **粗排（可插拔两级）**：默认 TF-IDF 余弦（0.6）× 饱和归一 BM25（0.4）线性融合（纯 Python Okapi BM25，k1=1.5 / b=0.75，离线可复现）；配置 Embedding 端点后升级为 **0.5×lexical + 0.5×向量余弦** 混合评分（OpenAI 兼容 `/v1/embeddings`，设置页或 `GEZHI_EMB_*` 环境变量配置，磁盘缓存按内容哈希、换模型自动失效）；未配置或端点失败时自动降级 lexical，离线行为逐比特不变；
3. **精排**：LLM 语义终审保留否决权（否决后不足 5 篇放弃否决，防空池）。

`scripts/eval_retrieval.py` 比较 lexical 与混合模式；`scripts/run_eval_set.py` 运行离线评测课题集（4 课题 + 1 跑题对照）。执行前按 [隔离验证](docs/harness-product-0.8.0.md#隔离验证) 设置数据目录，并为支持 `--out` 的入口显式指定报告路径。

## 系统自检（对标顶级智能体的 /doctor）

- 终端版 REPL：`/doctor`；桌面版：设置页 →「系统自检」；终端版菜单：系统自检
- 命令行：`python -m src.doctor`
- 检查项：数据目录可写、核心依赖、OpenAlex 文献源连通性、LLM 端点连通性与模型清单核对、提示词完整性、MCP 工具生态、研究档案库（git）、检索缓存占用、断点存档数量、Embedding 向量检索（未配置提示 lexical 兜底）

## 快速开始

以下命令从项目根目录运行，正常使用会保存研究记录和演示产物；验证请使用后面的隔离步骤。

```powershell
# 1. 安装依赖
python -m venv .venv
$gezhiVenvScripts = Join-Path (Get-Location).Path ".venv\Scripts"
$env:Path = "$gezhiVenvScripts;$env:Path"
python -m pip install -r requirements.txt

# 2. 系统自检（推荐首次运行）
python scripts/gezhi.py doctor

# 3. 桌面版
python scripts/gezhi.py desktop

# 4. 终端版（需 Node.js ≥ 20，默认进入 REPL 工作台）
npm.cmd --prefix tui ci
npm.cmd --prefix tui start

# 5. 命令行端到端演示（含人工干预与断点续跑，默认确定性离线样例）
python scripts/gezhi.py demo "large language model agents" --out data/exports/release_demo

# 需要实时 OpenAlex 检索时显式开启在线模式
python scripts/gezhi.py demo "large language model agents" --online --out data/exports/online_demo

# 6. Agent Harness：先规划，不执行副作用
python scripts/gezhi.py agent "调研大模型智能体并生成综述" --mode plan

# 7. Agent Harness：执行模式（写入/网络工具会请求审批）
python scripts/gezhi.py agent "调研大模型智能体并生成综述"

# 8. 跨进程批准/拒绝工具调用
# 将下面两个字符串替换为实际 ID
python scripts/gezhi.py approve "会话ID" "工具调用ID"
python scripts/gezhi.py deny "会话ID" "工具调用ID"

# 9. 专项智能体编制表 / 只跑文献猎手 / 子智能体流水线
python scripts/gezhi.py agents
python scripts/gezhi.py agent "调研大模型智能体" --agent literature --mode plan
python scripts/gezhi.py agent "调研大模型智能体" --agent pipeline

```

发布前测试、自检和离线评测使用 [隔离验证步骤](docs/harness-product-0.8.0.md#隔离验证)。旧 `scripts/verify_release.py` 有固定 `data/exports` 写入位置，仅设置 `GEZHI_DATA_DIR` 不能隔离其全部输出。

上述 Windows 命令无需修改 PowerShell 执行策略。macOS/Linux 使用 `source .venv/bin/activate` 替代 Windows 路径设置，并将 `npm.cmd` 改为 `npm`。

Windows 用户也可以安装 `dist/SciZhi-Agent-Setup-0.9.0.exe` 后从开始菜单启动双端。默认演示使用内置公开语义样例，保证答辩现场无网络也能稳定复现；在线模式才访问 OpenAlex。没有配置大模型时，系统使用规则算法完成抽取和综述；接入元景万悟或其他 OpenAI 兼容端点后，文献语义终审和综述生成会自动升级。

## Agent Harness 工作台

`src/agent_runtime.py` 提供统一的 Plan/Agent/Review 运行时：工具调用、权限请求、JSONL 会话和离线降级均由同一内核处理。专项智能体编制表在 `src/agents/specs.py`，执行体在 `src/agents/specialists.py`。

会话授权按 session 隔离；重启后审批从事件记录恢复，已处理请求不能再次执行。按会话的文件锁使多个进程的审批互斥，不同会话仍可并行。工具失败会返回失败终态并停止后续步骤。会话列表采用有界投影缓存，审批读取不使用缓存。打开历史会话会同时恢复计划、状态与待确认动作。

| 智能体 | 模式 | 职责 |
|---|---|---|
| `research` | primary | DAG + HITL 全流程总调度 |
| `pipeline` | primary | 文献→抽取→综述→引用→审计（无 HITL 节点） |
| `literature` | subagent | F1 文献猎手 |
| `extraction` | subagent | F2 结构化抽取 |
| `synthesis` | subagent | F2 综述框架（plan-act-reflect） |
| `stats` | subagent | F3 数据速览 |
| `citation` | subagent | F4 规则排版，禁止 LLM 生成格式 |
| `critic` | subagent | 只读证据审计 |
| `explore` | subagent | 编制表与工作区快照 |

TUI：`/plan` `/agent` `/spawn` `/review` `/agents` `/approve` `/deny` `/sessions`。Bridge：`agents`、`agent_plan`、`agent_run`、`agent_approve`、`agent_deny`、`sessions`。会话日志位于 `data/sessions/`。

## 打包与安装包

```powershell
# 一键完成 PyInstaller 打包 + NSIS 安装包（推荐）
scripts\build_desktop.bat
# 产出：dist\SciZhi-Agent-Setup-0.8.0.exe（免管理员安装到用户目录，
#       含桌面快捷方式、开始菜单项与卸载程序）
```

Windows 打包需安装 `requirements-dev.txt` 中的 PyInstaller、NSIS 3，并从 [Flet 0.86.5 官方发行页](https://github.com/flet-dev/flet/releases/tag/v0.86.5) 下载 `flet-windows.zip` 到 `assets/`；`TEMP`／`TMP` 的完整路径需只含 ASCII 字符。源码 ZIP 不附带第三方运行时；构建后桌面包带有所需运行资源。构建会重建当前版本的冻结目录，已有同名安装器拒绝覆盖。

## 接入大模型（可选）

系统无 LLM 也可完整运行（自动降级为确定性规则算法）。配置 OpenAI 兼容端点
（元景万悟平台模型或本地 vLLM）后，文献相关性终审与综述生成自动升级。
两种方式：桌面版“设置”页填写（持久化），或环境变量：

```bash
export GEZHI_LLM_BASE_URL="https://你的端点/v1"
export GEZHI_LLM_API_KEY="你的密钥"        # 可选：本地 vLLM 等无鉴权端点可留空
export GEZHI_LLM_MODEL="模型名"
# 可选双轨路由（未设则回落 GEZHI_LLM_MODEL）：
export GEZHI_LLM_MODEL_FAST="轻量模型名"    # 粗筛/批量抽取
export GEZHI_LLM_MODEL_STRONG="强模型名"    # 语义终审/综述生成
# 可选：Embedding 向量检索（RAG 语义级；不配置则 lexical 兜底）
export GEZHI_EMB_BASE_URL="https://你的端点/v1"
export GEZHI_EMB_API_KEY="你的密钥"
export GEZHI_EMB_MODEL="向量模型名"
```

完整接入步骤（端点申请 → 三种配置方式 → 验证四步 → 故障对照表）见 `docs/LLM接入指南.md`。

接入后自动生效：分层系统提示词（`src/prompts.py`，prompt_key 版本化留痕）、
JSON 输出自检重试、综述框架 plan→act→reflect 自主智能体与支撑文献防幻觉校验。

没有端点也能先看效果：`python scripts/demo_llm_run.py "研究方向"` 会起本地 Mock 端点，
走完全相同的调用路径并写出 `model_calls.jsonl` 样例。

## 目录结构

```
src/
  config.py            # 全局配置（含打包后路径适配）
  pdf_text.py          # PDF 全文提取（pypdf/标准库双后端，材料通道，失败如实报错）
  settings.py          # 运行设置持久化（界面可改，即时生效）
  service.py           # 双端共用的任务服务层（含取消/重跑/删除/导出清单）
  doctor.py            # 系统自检（依赖/网络/端点/提示词/缓存一键体检）
  aigc.py              # AIGC 显式标识（生成元数据 + 导出 manifest，合规）
  ranking.py           # 确定性排序层（Okapi BM25 + 融合评分 + RRF 工具，离线可复现）
  embedding.py         # Embedding 向量检索（OpenAI 兼容 /v1/embeddings，磁盘缓存，可插拔降级）
  agent_runtime.py     # Agent Harness（Plan/Agent/Review + 子智能体 spawn/fan-out + JSONL 会话）
  bridge.py            # Python↔Node stdio 桥（JSON Lines）
  llm.py               # LLM 客户端（OpenAI 兼容：模型探测/流式/双轨路由）
  prompts.py           # 分层系统提示词工程（prompt_key 版本化留痕）
  logging_util.py      # 模型/工具调用 JSONL 日志（参赛材料直接用）
  plugins.py           # 插件发现器（plugins/ → REPL 命令 + 工作流动作）
  skills.py            # 技能包系统（SKILL.md → 系统提示词注入）
  context_file.py      # GEZHI.md 研究上下文（对标 AGENTS.md）
  mcp.py               # MCP stdio 客户端（在线工具 → 工作流动作）
  research_repo.py     # 研究档案库（产物 git 版本化）
  orchestrator/        # 通用智能体工作流引擎（DAG + Checkpoint + HITL）
  connectors/          # OpenAlex / CrossRef 公开数据源连接器
  agents/              # 编制表 + 文献猎手 / 抽取 / 综述 / 统计 / 审计执行体 + 本地文献入池
  citation/            # 格式管家（GB/T 7714 规则引擎）
  ui/brand.py          # 品牌视觉（文房五色 + 文案）
  ui/desktop.py        # 桌面版（Flet）
  ui/tui.py            # 备用本地 TUI（Textual，进程内直连）
tui/                   # 终端版（Node.js + OpenTUI，REPL 命令工作台）
plugins/               # 插件目录（bibtex_export / term_freq，一文件一插件）
skills/                # 技能包目录（academic-writing / lit-critique）
GEZHI.md               # 研究上下文文件（/init 生成，用户声明研究偏好）
workflows/research_pipeline.yaml   # 工作流声明式配置（参赛材料）
packaging/gezhi.nsi                # NSIS 安装包脚本
examples/experiment_demo.csv       # 内置示例实验数据
examples/eval_topics.yaml          # 离线评测课题集（4 课题 + 1 跑题对照）
docs/赛题与评审要点.md              # 赛题原文 + 逐条对照自查表
docs/开发接力_技术方案.md           # 开发交接文档
docs/LLM接入指南.md                # 元景万悟 / OpenAI 兼容端点接入实操
data/                  # 运行时：checkpoints / artifacts / logs / exports / cache
```

## 参赛材料对应

- 通用智能体流程配置文件：`workflows/research_pipeline.yaml`
- 工具调用日志：`data/logs/tool_calls.jsonl`；模型调用日志：`data/logs/model_calls.jsonl`（含 prompt_key 提示词版本留痕）
- 系统原型：桌面版安装包 + Node TUI，双击即用
- 演示产物样例：`data/artifacts/demo_*/` 与 `data/exports/`

## 性能与响应（实测）

| 场景 | 实测 | 做法 |
|---|---|---|
| 引擎就绪 | **0.10 s**（此前 2.25 s） | 重依赖（httpx/pandas/sklearn/matplotlib）惰性导入；桥 init 后发 `{"ready": true}` 握手，界面显示「引擎启动中…」并排队请求 |
| 轮询稳态负载 | **0.2 KB/次**（首帧 131 KB） | `poll` 携带 `art_rev` 产物版本号：版本未变只回状态/进度/事件，重字段按需下发（`full: true` 可强制全量） |
| 同主题复跑 | 1.5 s（首次 37 s） | OpenAlex 连接池 + 24h 磁盘响应缓存 + 引用/被引并发扩展 |
| 任务列表 | 0.1 ms（此前 202 ms / 92 任务） | 工作流 YAML 只读缓存（`lru_cache`）：原先每个任务都要重读重解析 YAML，而它被桌面首屏、桌面轮询与 TUI `/tasks` 反复调用 |
| 轮询节奏 | 运行中 0.9 s / 待人工·暂停 2.5 s / 终态 5 s 心跳 | 自适应自调度，空闲不再空转；状态类命令执行后立即刷新一次；桌面版按变更签名门控重绘，长任务事件控件上限 800 条 |
| 单元测试 | 525 例 / 约 25 s（冷启动约 30 s） | 离线、零外部依赖（较慢的用例来自 Mock LLM 端点与 MCP 握手的子进程） |
| 桥命令往返 | 中位数 ≤ 0.5 ms（`usage` 1.6 ms） | `poll/board/graph/ledger/list` 各 20 次实测；`usage` 要聚合 JSONL 调用日志故略慢，仅启动状态行与 `/usage` 调用 |
| 断点档案规模 | 300 份存档（8.5 MB）冷启动读档 **112 ms** / `list_tasks()` 0.38 ms | 全量读档在 `service.init()` 一次性完成（15→100→300 份实测线性增长），无需惰性化；台账池统计 O(N) 遍历 |
| 桌面版空闲占用 | 启动到窗口 **1.8 s**；空闲 20 s 内 python 引擎 **0.000 s CPU**、flet 客户端 0.016 s（0.1% 单核）；常驻 **208 MB**（60 引擎 + 148 客户端） | 无选中任务时轮询只 sleep；有任务时按状态自适应节奏（运行 0.8 s / 中间 2.5 s / 终态 5 s）并按变更签名门控重绘——可长期常驻，不费电 |

命令执行期间 REPL 显示「处理中…」，任务运行中显示进度条、已用时间与**预计剩余时间**（按已完成节点均速外推）；到达人工确认关口或任务完成时会主动给出下一步建议，常见接口故障（限流/超时/鉴权）会附带可操作提示。

## 工程质量

- `tests/`：Python 单元测试 525 例（工作流打桩流转 / Checkpoint 往返 / 干预版本化 / 增量轮询协议 / LLM 客户端对 Mock 端点端到端 / 证据图谱 / **双源并行检索** / **权限策略持久化** / **池内引用网络** / 引用排版 / 提示词容错 / 插件 / 技能 / MCP 握手 / 研究档案库 / BM25 / AIGC 标识 / 用量统计 / 科研看板 / **终端排版宽度** / **旧档日期兜底** / **图谱截断如实报规模** / **预览演示任务注入闭环** / **PDF 证据链**（双后端提取、附件集成、本地入池、全文抽取 28 例）），`python -m unittest discover -s tests -t . -v`，离线零依赖；本轮新增：**底座韧性**（上下文压缩/自愈/并行/中断对账 11 例）、**Embedding 与漏斗**（24 例）、**缺证据补检**（8 例）、**在线 fanout**（4 例）、**DAG 并行与 condition**（12 例）、**评测集**（6 例）
- `scripts/mock_llm_server.py` + `scripts/demo_llm_run.py`：OpenAI 兼容的本地 Mock 端点，离线跑通"LLM 参与"的抽取与综述框架并产出 `model_calls.jsonl` 样例（含 prompt_key 与快/强轨分流证据）
- 当前测试入口见 [0.8.0 隔离验证](docs/harness-product-0.8.0.md#隔离验证)；旧 `scripts/verify_release.py` 保留为历史入口，不用于隔离验收。
- `tui/test/`：四套 TUI 测试串在 `npm test` 里——`smoke.js` **15 项**（握手/首屏/命令表/补全/面板/**行内编辑**/**清屏**/忙碌/阅读器/历史/菜单）、`menu.js` **3 项**（HITL 编号菜单端到端，含纯函数校验）、`reader.js` **5 项**（全屏阅读器窗口完整性：表头区间 == 实际渲染行号、翻页/逐行/到底不越界）、`width.js` **10 项**（CJK 列宽工具）；写入按键一律用 `type()`/`writeUntil()`/`retryUntil()` 确认生效
- `tui/test/reader.js`：全屏阅读器的**窗口不变量**测试（隔离数据目录 + 60 行长文）——盒子高度算错时 ink 会静默压掉子行（行号断档），此测试即用「表头 `行 a–b / total` == 实际渲染的行号序列」把它钉死
- `tui/test/smoke.js`：TUI 冒烟测试 **15 项**（引擎握手 / 首屏 / `/help` / Tab 补全 / **补全面板** / **Enter 补全** / **Esc 清空** / **Ctrl+C 提示** / `/read` 用法 / 忙碌指示 / 阅读器（行号+搜索）/ ↑ 历史召回 / 菜单），条件等待断言；另有 `tui/test/width.js` 10 项覆盖 CJK 列宽工具。写入按键用 `type()`/`writeUntil()` 确认生效——ink-testing-library 在渲染忙时会丢键，直接写会得到假阴性
- `node tui/scripts/preview_tui.mjs`：**TUI 外观预览生成器**——14 个界面渲染成文本帧存到 `design-previews/tui/<时间戳>/`：静态界面（首屏/命令表/任务跟踪/看板/图谱/阅读器/搜索态/时间账/菜单）之外，还通过桥的 `demo_seed` 注入一条**离线演示任务**抓下**动态过程帧**（HITL 人工确认关口的编号菜单 → 池复核 → 放行后节点依次跑完），抓完 `demo_clean` 清理、不在真实档案留痕——答辩最想展示的"暂停键"瞬间从此可复核、可复现
- `python scripts/lint_ui.py`：UI 静态校验（flet 参数名/图标常量/自定义函数签名、协程型 API 调度、FilePicker 注册为 service、长文本 width/max_lines 六类检查），发布验收已含此 gate
- **UI 体检网**：`scripts/lint_ui.py`（AST 静态校验：自定义函数签名、flet 控件参数名、Icons/Colors 常量、**FilePicker 等协程是否交给 run_task**）、`tests/test_desktop_build.py`（stub Page 跑一遍 `main()` 构建全控件树）、`tests/test_desktop_handlers.py`（点导航逐页遍历，把所有事件回调各跑一遍）——三者都进发布验收
- `python -m src.doctor`：无头环境自检
- 桌面版轮询渲染带变更检测（数据不变不重绘）；数据分析在后台线程执行，不阻塞界面
- TUI 经 `since` 增量拉事件、`art_rev` 增量拉产物，稳态零重字段传输
