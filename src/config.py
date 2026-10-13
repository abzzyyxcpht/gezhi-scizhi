"""格致 SciZhi 全局配置。

所有可通过环境变量覆盖，默认即可离线演示（无 LLM Key 时自动降级为规则算法）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---- 目录 ----
# 打包（PyInstaller）后：只读资源走 _MEIPASS，可写数据走 %LOCALAPPDATA%/Gezhi
_FROZEN = getattr(sys, "frozen", False)
ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
# 数据目录优先级：GEZHI_DATA_DIR 环境变量 > 打包目录(%LOCALAPPDATA%/Gezhi) > 仓库 data/
# 环境变量这条既是部署灵活性（共享机器/演示隔离），也让测试能把数据写到临时目录，
# 避免每跑一次测试就往仓库 data/ 里堆断点与图表。
_env_data = os.getenv("GEZHI_DATA_DIR")
if _env_data:
    DATA_DIR = Path(_env_data)
elif _FROZEN:
    DATA_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "Gezhi" / "data"
else:
    DATA_DIR = ROOT_DIR / "data"
CHECKPOINT_DIR = DATA_DIR / "checkpoints"   # 工作流断点存档
ARTIFACT_DIR = DATA_DIR / "artifacts"       # 任务产物（文献池/综述/图表等）
LOG_DIR = DATA_DIR / "logs"                 # 模型调用与流程执行日志
UPLOAD_DIR = DATA_DIR / "uploads"           # 用户上传的实验数据
for _d in (CHECKPOINT_DIR, ARTIFACT_DIR, LOG_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- LLM（OpenAI 兼容协议，可指向元景万悟/本地 vLLM 等） ----
LLM_BASE_URL = os.getenv("GEZHI_LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("GEZHI_LLM_API_KEY", "")
LLM_MODEL = os.getenv("GEZHI_LLM_MODEL", "")
LLM_MODEL_FAST = os.getenv("GEZHI_LLM_MODEL_FAST", "")      # 双轨路由·快模型（粗筛/批量）
LLM_MODEL_STRONG = os.getenv("GEZHI_LLM_MODEL_STRONG", "")  # 双轨路由·强模型（终审/综述）
LLM_TIMEOUT = float(os.getenv("GEZHI_LLM_TIMEOUT", "60"))
LLM_PROTOCOL = os.getenv("GEZHI_LLM_PROTOCOL", "chat_completions")
LLM_AUTH_STYLE = os.getenv("GEZHI_LLM_AUTH_STYLE", "auto")
LLM_THINKING_STYLE = os.getenv("GEZHI_LLM_THINKING_STYLE", "auto")
LLM_REASONING_EFFORT = os.getenv("GEZHI_LLM_REASONING_EFFORT", "auto")
LLM_THINKING_BUDGET = int(os.getenv("GEZHI_LLM_THINKING_BUDGET", "4096"))
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("GEZHI_LLM_MAX_OUTPUT_TOKENS", "8192"))
LLM_CONTEXT_WINDOW = int(os.getenv("GEZHI_LLM_CONTEXT_WINDOW", "32768"))
LLM_CACHE_ENABLED = os.getenv("GEZHI_LLM_CACHE_ENABLED", "1").lower() not in {"0", "false", "off"}

# ---- 文献猎手（F1）参数 ----
LIT_PER_PAGE = int(os.getenv("GEZHI_LIT_PER_PAGE", "25"))      # 每轮每源抓取量
LIT_MAX_ROUND = int(os.getenv("GEZHI_LIT_MAX_ROUND", "3"))     # 递归最大轮次
LIT_SCORE_THRESHOLD = float(os.getenv("GEZHI_LIT_TAU", "0.18"))  # 相关性保留阈值
LIT_CONVERGE_RATIO = float(os.getenv("GEZHI_LIT_CONV", "0.05"))  # 收敛：新增相关占比
LIT_SNOWBALL_TOPK = int(os.getenv("GEZHI_LIT_TOPK", "10"))     # 滚雪球种子数
LIT_ENABLE_CROSSREF = os.getenv("GEZHI_LIT_CROSSREF", "1") == "1"  # 双源并行检索

# ---- 服务 ----
HOST = os.getenv("GEZHI_HOST", "127.0.0.1")
PORT = int(os.getenv("GEZHI_PORT", "8000"))
