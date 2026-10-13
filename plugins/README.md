# plugins/ —— 格致插件目录

一个 Python 文件即一个插件（或 `子目录/plugin.py`）。模块级 `PLUGIN` 字典：

```python
PLUGIN = {
    "name": "myplug",                      # 全局唯一（小写）
    "version": "1.0",
    "description": "一句话说明",
    "commands": {                          # → REPL 斜杠命令（引擎侧执行）
        "mycmd": {"help": "…", "run": fn}, # fn(args: str, ctx|None) -> str
    },
    "actions": {                           # → 工作流动作（YAML 直接引用）
        "plug_my_action": fn,              # fn(ctx, params) -> dict
    },
}
```

- 命令在 REPL 里直接 `/mycmd args` 调用，`ctx` 为当前打开任务的 TaskContext（可能为 None）；
- 动作写进 `workflows/*.yaml` 的 `action:` 字段即可进入流水线；
- 下划线开头的文件被跳过；导入失败只记录不炸主程序（`/plugins` 可见错误）。

内置：`bibtex_export.py`（/bib，LaTeX/Zotero 工作流）、`term_freq.py`（/freq，综述焦点分析）。
