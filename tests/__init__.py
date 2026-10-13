# -*- coding: utf-8 -*-
"""格致 SciZhi 单元测试（stdlib unittest，零新依赖，离线可跑）。

运行：python -m unittest discover -s tests -v

测试隔离：本模块在被 discover 导入时（早于任何 src 导入）把运行时数据目录
指向临时目录，因此测试不会往仓库 `data/` 里堆断点存档与图表产物。
需要真实数据目录时设置 `GEZHI_DATA_DIR` 即可覆盖（会被尊重，不再替换）。
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

if not os.environ.get("GEZHI_DATA_DIR"):
    _TMP_DATA = tempfile.mkdtemp(prefix="gezhi_tests_")
    os.environ["GEZHI_DATA_DIR"] = _TMP_DATA
    atexit.register(lambda: shutil.rmtree(_TMP_DATA, ignore_errors=True))
