# 参与贡献

使用 Python 3.11+、Node.js 20+。先安装 `requirements.txt`、`requirements-dev.txt`，再在 tui 目录运行 `npm ci`。

提交前运行 `python scripts/run_ci.py` 和 `python scripts/verify_release.py`。运行时变化须覆盖审批、取消、失败和重启恢复；界面变化须检查真实截图与窄窗口。使用 `GEZHI_DATA_DIR` 指向临时目录，避免测试污染研究记录。

请通过 Issue 描述可复现问题，通过 Pull Request 提交改动。贡献表示你有权将改动按 Apache-2.0 发布。不要提交密钥、私人文献、研究数据或个人会话。
