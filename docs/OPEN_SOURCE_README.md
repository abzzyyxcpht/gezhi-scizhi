# 格致 SciZhi

格致 SciZhi 是一个面向科研过程的本地优先智能体工作台：把文献检索、证据抽取、综述组织、人工审查和研究归档串成可暂停、可追溯、可复现的流水线。

## 公开仓库

<https://github.com/abzzyyxcpht/gezhi-scizhi>

仓库持续更新，采用 Apache-2.0 许可，聚焦 Agent 与科研工作台。界面设计规范见 `DESIGN.md`，共享偏好定义见 `assets/ui-design.json`。

## 安全与隐私边界

- 使用本地工作区，模型端点由用户配置；公开源码不包含实际密钥。
- 研究数据、缓存、运行日志和断点档案不进入公开源码包。
- 工具权限分为只读、工作区写入和完全访问，写入与网络操作经过人工确认。
- 每次导出都生成 `manifest.md` 与 `data.sha256`，便于核对来源、变更和回滚。
- 公开版本只包含源码、文档和脱敏样例；真实研究数据、个人路径和凭据不入库。

## 快速开始

```powershell
python -m pip install -r requirements.txt
python scripts/gezhi.py doctor
python scripts/gezhi.py demo "large language model agents"
```

启动桌面端（Python 3.13）：

```powershell
python scripts/gezhi.py desktop
```

启动终端端（Node.js 24）：

```powershell
npm --prefix tui ci
npm --prefix tui start
```

外观设置支持跟随系统、浅色、深色主题；界面、代码和桌面内置终端字号独立调整。代码预览支持行号、语法配色与横向滚动。TUI 使用 `/config` 调整共享偏好，`Ctrl+B` 开关侧栏，`Esc` 关闭浮层。外部终端的字体与物理字号由终端宿主设置。

## 贡献与报告

欢迎通过 GitHub Issues 或 Pull Request 提交问题、改进和复现记录。安全问题请先阅读 `SECURITY.md`，不要在公开 issue 中粘贴密钥、真实数据或未脱敏日志。
