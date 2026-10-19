# 会话执行计划与完成检查：开源实现研究

核验日期：2026-10-19。用途：SciZhi 0.11.0 的设计依据。本文记录代码研究和本轮方案，不替代产品测试报告，不构成与其他产品的性能排名。

仅查阅维护方仓库中的源码、提交与许可证。通过 GitHub API 取得默认分支 HEAD，并使用完整提交号固定后续文件读取；没有下载或执行上游工程。下述实现由 SciZhi 独立编写，不引入这些框架依赖，也不复制其源码或提示词。

## 核验快照

| 项目 | 分支与完整提交号 | 许可证 |
| --- | --- | --- |
| LangChain Deep Agents | `main` · `ca48a2b49fd3a542ca609037f8e22d56a18745d1` | [MIT](https://github.com/langchain-ai/deepagents/blob/ca48a2b49fd3a542ca609037f8e22d56a18745d1/LICENSE) |
| LangChain，供核对可选 Todo 中间件 | `master` · `4fe944a581895ab47b0f56e204829594d5ba74a8` | [MIT](https://github.com/langchain-ai/langchain/blob/4fe944a581895ab47b0f56e204829594d5ba74a8/LICENSE) |
| OpenHands Software Agent SDK | `main` · `3e8e17bee85494eb5ec2eae71ad0bb011e35b33d` | [MIT](https://github.com/OpenHands/software-agent-sdk/blob/3e8e17bee85494eb5ec2eae71ad0bb011e35b33d/LICENSE) |
| OpenCode | `dev` · `cec70f3ed6b64bf0e23a7c62325850ca25d3409b` | [MIT](https://github.com/anomalyco/opencode/blob/cec70f3ed6b64bf0e23a7c62325850ca25d3409b/LICENSE) |

MIT 是以上所列仓库根许可证的核验结果，并非对其所有第三方依赖的许可判断。

## 1. Deep Agents：停止前检查完成标准

当前 `RubricMiddleware` 在智能体准备结束、模型没有继续请求工具时启动另一个评估智能体。评估返回 `satisfied`、`needs_revision` 或 `failed`；需要修改时把反馈带回执行循环。次数上限、评估调用失败和未通过必须分别记录。默认上限为三次；调用方仍须读取评估状态，不能仅凭最后一条回答宣称已验收。[固定源码：rubric.py](https://github.com/langchain-ai/deepagents/blob/ca48a2b49fd3a542ca609037f8e22d56a18745d1/libs/deepagents/deepagents/middleware/rubric.py#L660)

该实现会保留第一次非空的标准列表，并检查后续评估是否漏掉标准；源码明确说明其覆盖检查主要比较数量，不能保证第一次拆分完整，也不检查名称完全一致。这是有界的模型评估机制，不是结论正确性的证明。[覆盖检查及其边界](https://github.com/langchain-ai/deepagents/blob/ca48a2b49fd3a542ca609037f8e22d56a18745d1/libs/deepagents/deepagents/middleware/rubric.py#L872)

创建入口把可选 `checkpointer` 和 `store` 交给底层智能体图；这与执行计划或评估逻辑分开。[固定源码：graph.py](https://github.com/langchain-ai/deepagents/blob/ca48a2b49fd3a542ca609037f8e22d56a18745d1/libs/deepagents/deepagents/graph.py#L293)

**版本变化需要明确：** Deep Agents 0.7.0 的变更记录已移除默认 `TodoListMiddleware`、`write_todos` 和 `todos` 状态。需要显式添加才能恢复，不能描述为当前默认能力。[固定变更记录](https://github.com/langchain-ai/deepagents/blob/ca48a2b49fd3a542ca609037f8e22d56a18745d1/libs/deepagents/CHANGELOG.md)

LangChain 仍提供可选 `TodoListMiddleware`：`write_todos` 替换完整列表，并在同一次模型响应包含多次计划写入时拒绝这些更新，避免全量替换产生覆盖歧义。完成要求主要写在工具说明中，不能将勾选条目等同于独立验收。[固定源码：todo.py](https://github.com/langchain-ai/langchain/blob/4fe944a581895ab47b0f56e204829594d5ba74a8/libs/langchain_v1/langchain/agents/middleware/todo.py)

**对本项目的启发：** 把“模型停止回答”和“计划状态允许结束”分开；完成检查保留明确结果与有限回补次数。SciZhi 本轮采用确定性工具回执检查，不增加一个模型评委。

## 2. OpenHands：任务状态、会话持久化分别处理

`TaskTrackerTool` 提供 `view`、`plan`，条目包含标题、备注和状态。`plan` 替换列表，可按会话持久化目录写入 `TASKS.json`；工具重新创建时读取该文件。其持久化失败只记日志，读取损坏文件会退为空列表，不能直接作为本项目的严格保存语义。[固定源码：definition.py](https://github.com/OpenHands/software-agent-sdk/blob/3e8e17bee85494eb5ec2eae71ad0bb011e35b33d/openhands-tools/openhands/tools/task_tracker/definition.py#L166)

会话本身保存基础状态与事件，恢复时核对会话身份和工具兼容性；公开字段赋值触发保存，嵌套字典需要重新赋值才能触发。恢复已保存会话不是重新运行一次任务。[固定源码：state.py](https://github.com/OpenHands/software-agent-sdk/blob/3e8e17bee85494eb5ec2eae71ad0bb011e35b33d/openhands-sdk/openhands/sdk/conversation/state.py)

其 `AgentFinishedCritic` 检查最后一个动作是否为 `FinishAction`，以及 git patch 是否非空。这种面向代码改动的判据不能原样用于文献检索、数据分析或问答，也不能证明改动正确。[固定源码：agent_finished.py](https://github.com/OpenHands/software-agent-sdk/blob/3e8e17bee85494eb5ec2eae71ad0bb011e35b33d/openhands-sdk/openhands/sdk/critic/impl/agent_finished.py)

**对本项目的启发：** 执行计划随会话恢复；界面只展示后端记录。保持已有原子写入、会话锁、审批和工具执行日志，不另建一套有分歧的持久化路径。

## 3. OpenCode：只引用历史 Todo 设计，不当作当前功能

当前默认分支已经没有 Todo 工具和对应运行时。维护方在 2026-07-09 的提交 `7feefb697f279fe08677512f08abf72a9f418d5a` 删除了该功能。历史材料和旧截图无法代表本次核验的版本。[删除提交](https://github.com/anomalyco/opencode/commit/7feefb697f279fe08677512f08abf72a9f418d5a)

删除前父提交为 `d4155f290680506d80b76dc7cda5e24820956d3b`：Todo 按会话存储，全量替换在数据库事务内完成，成功后发布更新事件。工具调用路径先执行权限判断再修改列表。这可作为“持久化完成后发布界面事件”的历史设计参考；并无据此证明任务执行质量提升。[历史状态实现](https://github.com/anomalyco/opencode/blob/d4155f290680506d80b76dc7cda5e24820956d3b/packages/opencode/src/session/todo.ts)、[历史工具实现](https://github.com/anomalyco/opencode/blob/d4155f290680506d80b76dc7cda5e24820956d3b/packages/opencode/src/tool/todo.ts)

## SciZhi 现状与本轮适配边界

研究开始时的本地代码已经具备：

- `src/research_planning.py`：科研待办、计划调度与通知。这属于用户工作台管理，并非模型每次执行的进度证明。
- `src/agent_runtime.py`：确定性步骤计划、`plan_created` 日志、审批与工具参数检查；`_execute_call` 写入含调用身份及执行结果的 `tool_call`、`tool_result`。
- `src/agent_driver.py`：有界模型循环、持久消息、压缩、失败回补、只读并发与中断对账。模型不再请求工具时，原逻辑检查未解决工具错误与空回答后结束，尚未核对会话执行计划。

因此本轮方案集中补齐“会话执行计划 → 实际工具回执关联 → 结束前有界检查”，复用现有 Python 引擎，并供 Flet 与终端界面共同展示。

| 边界 | 本轮设计要求 |
| --- | --- |
| 计划来源 | 模型可整理执行步骤，步骤文字不产生新增权限；仍由现有工具策略决定是否可执行。 |
| 回执来源 | 完成关联只接受本会话执行日志中的真实调用身份与成功结果，拒绝模型自填成功消息、其他会话 ID 和失败回执。 |
| 状态更新 | 计划持久化、事件展示和恢复须一致；并发写入不得覆盖更新，重试不得重复执行工具。 |
| 完成检查 | 有未完成步骤或关联不满足条件时给出可行动反馈；回补有上限，并继续受原步骤数、token、取消及审批约束。 |
| 简单问答 | 没有采用执行计划时保持直接回答，避免把所有请求强制展开成多步任务。 |
| 展示措辞 | 展示“关联的执行回执”“计划完成状态”等可核验事实，不显示未经实现的独立科学审核或质量保证。 |

**确定性回执检查与模型 Rubric 评估的区别：** 本项目检查的是“引用的工具调用确实属于本会话，并由运行时记录为成功”等结构化事实；Deep Agents Rubric 由另一模型判断材料是否满足文字标准。两者都不能据此证明论文结论真实、统计设计合理或模型推理正确。成功工具回执也未必与步骤语义充分相关；界面应让用户能查看关联工具与结果，最终验收仍以具体任务证据为准。

本轮不宣称已全面超过上述项目，不用计划面板代替功能测试；具体落地能力、兼容范围和测试结果以 0.11.0 实现及发布记录为准。
