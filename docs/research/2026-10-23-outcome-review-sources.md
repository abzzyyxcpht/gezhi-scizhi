# 成果复核与双端交互研究

核验日期：2026-10-23。以下为维护方文档与官方源码，阅读用于识别工程机制；未运行上游客户端进行性能排名，未复制上游实现。

| 来源 | 已核验机制 | 本项目采用方式 |
| --- | --- | --- |
| [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) | 批准关联审查时的差异；更新可使旧审查失效，要求修改与批准分开 | 成果指纹、显式决定、保存时比较版本 |
| [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) | 持久检查点保留状态，更新产生新检查点；同步和异步持久化有不同故障边界 | 复用已有本地锁和原子写入，保留有限复核历史；不引入 LangGraph |
| [Codex App Server](https://learn.chatgpt.com/docs/app-server) | 请求提出、解决与最终任务完成是不同状态，失败有独立表达 | 展示保存中、确认成功、明确失败与结果不确定；校验任务和成果版本 |
| [Carbon inline loading](https://www.carbondesignsystem.com/building-blocks/core/components/inline-loading/guidelines) | 保存期间防止重复触发，成功和失败反馈对应实际结果 | 后台保存与任务级在途互斥，不用按钮点击代替成功回执 |
| [Carbon InlineLoading 源码](https://raw.githubusercontent.com/carbon-design-system/carbon/main/packages/react/src/components/InlineLoading/InlineLoading.tsx) | 活动、成功、失败分开渲染，定时器清理；读取时默认播报为 assertive | 只采用实际状态反馈原则，不照搬网页组件或宣称全局无障碍合规 |
| [Anthropic long-running harnesses](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) | 单元测试不足以证明用户完整操作路径可用，需要端到端核对 | 原生面板到桥接和真实存储的集成测试，加桌面截图与窄终端操作测试 |

当前项目已有事件分页、任务恢复与确定性状态。本轮主要补齐真实产物模式、复核版本关系、存储与端侧接入，不重复实现工作流框架。没有采用新模型供应商、隐藏推理可视化、虚拟进度或无依据的效率指标。

动态文档与 Carbon main 源码可能变化，本记录不将当前行为视为永久接口保证。验收以本仓库固定实现和测试为准。
