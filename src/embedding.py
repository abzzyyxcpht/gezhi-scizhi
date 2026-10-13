"""Embedding 向量检索：RAG 三级漏斗的向量级（第二级）。

- ``EmbeddingProvider`` 抽象：``embed(texts)`` 返回与输入等长的向量列表，任何失败
  一律返回 ``None`` 而不抛异常，调用方据此静默降级为 lexical 评分；
- ``HttpEmbeddingProvider``：OpenAI 兼容 ``POST {base_url}/embeddings``，api_key 可选
  （兼容无鉴权 vLLM），超时 15s；base_url 已含 ``/v1`` 与否都兼容；
- 磁盘缓存：``data/cache/embeddings/<sha256(text)>.json``（state_store 原子写），
  命中即免请求；缓存记录携带模型名，模型更换自动失效；
- 未配置（缺 base_url 或 model）时 ``get_provider()`` 返回 ``None``，
  离线行为与未引入本模块前逐比特一致。

配置优先级：环境变量（GEZHI_EMB_BASE_URL / GEZHI_EMB_API_KEY / GEZHI_EMB_MODEL）
> settings.json（embedding_base_url / embedding_api_key / embedding_model）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from .config import DATA_DIR
from .state_store import write_json

_CACHE_DIR = DATA_DIR / "cache" / "embeddings"
_TIMEOUT = 15.0


class EmbeddingProvider(ABC):
    """向量提供方抽象：失败一律返回 None，不抛异常。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """返回与 ``texts`` 等长的向量列表；任何失败返回 None。"""


class HttpEmbeddingProvider(EmbeddingProvider):
    """OpenAI 兼容 /embeddings 端点的提供方，带磁盘缓存。"""

    def __init__(self, base_url: str, api_key: str = "", model: str = "") -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""

    def _endpoint(self) -> str:
        """base_url 已含 /v1（结尾或路径中）则直接拼 /embeddings，否则补 /v1。"""
        if self.base_url.endswith("/v1") or "/v1/" in self.base_url:
            return self.base_url + "/embeddings"
        return self.base_url + "/v1/embeddings"

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        cached: dict[int, list[float]] = {}
        missing: list[int] = []
        for i, text in enumerate(texts):
            vec = self._read_cache(text)
            if vec is not None:
                cached[i] = vec
            else:
                missing.append(i)
        if missing:
            batch = self._request([texts[i] for i in missing])
            if batch is None or len(batch) != len(missing) or not batch[0]:
                return None
            dim = len(batch[0])
            for pos, vec in zip(missing, batch):
                if not vec or len(vec) != dim:
                    return None  # 维度不一致或部分缺失：整批判失败，静默降级
                self._write_cache(texts[pos], vec)
                cached[pos] = vec
        return [cached[i] for i in range(len(texts))]

    def _request(self, texts: list[str]) -> list[list[float]] | None:
        """单次 HTTP 请求；网络/协议错误一律返回 None。"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(timeout=_TIMEOUT) as cli:
                resp = cli.post(self._endpoint(), headers=headers,
                                json={"model": self.model, "input": texts})
                resp.raise_for_status()
                data = resp.json()
            rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
            vectors = [row.get("embedding") for row in rows]
            if any(not isinstance(v, list) or not v or
                   any(not isinstance(x, (int, float)) for x in v) for v in vectors):
                return None
            return [[float(x) for x in v] for v in vectors]
        except Exception:  # noqa: BLE001 —— 提供方失败静默降级，绝不抛出
            return None

    def _cache_path(self, text: str) -> Path:
        return _CACHE_DIR / f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}.json"

    def _read_cache(self, text: str) -> list[float] | None:
        path = self._cache_path(text)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("model") != self.model:
                return None  # 模型更换：缓存失效
            vec = data.get("vector")
            if isinstance(vec, list) and vec and all(isinstance(x, (int, float)) for x in vec):
                return [float(x) for x in vec]
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _write_cache(self, text: str, vector: list[float]) -> None:
        try:
            write_json(self._cache_path(text), {"model": self.model, "vector": vector})
        except OSError:
            pass  # 缓存写失败不影响主流程


def cosine(query_vec: list[float], doc_vecs: list[list[float]]) -> list[float]:
    """query 与每篇文档的余弦相似度；零向量安全（该篇记 0.0）。"""
    qn = math.sqrt(sum(x * x for x in query_vec))
    out: list[float] = []
    for doc in doc_vecs:
        dn = math.sqrt(sum(x * x for x in doc))
        if not qn or not dn:
            out.append(0.0)
            continue
        dot = sum(a * b for a, b in zip(query_vec, doc))
        out.append(dot / (qn * dn))
    return out


_provider: EmbeddingProvider | None = None
_provider_key: tuple[str, str, str] | None = None


def get_provider(settings_dict: dict[str, Any] | None = None) -> EmbeddingProvider | None:
    """按 settings/env 惰性构建提供方；按配置签名缓存，签名变化即重建。

    缺 base_url 或 model（环境变量与 settings 均无）返回 None——
    此时检索漏斗退化为 lexical 级，行为与未引入本模块前一致。
    """
    global _provider, _provider_key
    if settings_dict is None:
        from . import settings as app_settings
        settings_dict = app_settings.load()
    base_url = (os.getenv("GEZHI_EMB_BASE_URL")
                or str(settings_dict.get("embedding_base_url") or "")).strip()
    api_key = (os.getenv("GEZHI_EMB_API_KEY")
               or str(settings_dict.get("embedding_api_key") or "")).strip()
    model = (os.getenv("GEZHI_EMB_MODEL")
             or str(settings_dict.get("embedding_model") or "")).strip()
    if not base_url or not model:
        return None
    key = (base_url, api_key, model)
    if _provider is not None and _provider_key == key:
        return _provider
    _provider = HttpEmbeddingProvider(base_url, api_key, model)
    _provider_key = key
    return _provider


def reset_provider() -> None:
    """清空惰性单例，供测试与设置变更后重建。"""
    global _provider, _provider_key
    _provider = None
    _provider_key = None


def clear_cache() -> None:
    """清空磁盘向量缓存，供测试与故障恢复。"""
    if _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR, ignore_errors=True)
