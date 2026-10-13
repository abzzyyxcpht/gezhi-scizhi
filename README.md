# 格致 SciZhi

格致 SciZhi 是一个面向科研过程的本地优先智能体工作台：把文献检索、证据抽取、综述组织、人工审查和研究归档串成可暂停、可追溯、可复现的流水线。

## 公开仓库

<https://github.com/abzzyyxcpht/gezhi-scizhi>

仓库持续更新，采用 Apache-2.0 许可。项目路演稿与关键演示材料位于 `docs/roadshow/`。

## 安全与隐私边界

- 默认本地运行，密钥只保存配置状态，不写入演示输出和版本库。
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

桌面端、终端端和离线演示的说明请参考根目录源码与 `README.md`。

## 贡献与报告

欢迎通过 GitHub Issues 或 Pull Request 提交问题、改进和复现记录。安全问题请先阅读 `SECURITY.md`，不要在公开 issue 中粘贴密钥、真实数据或未脱敏日志。
