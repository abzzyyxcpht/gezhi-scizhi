"""CrossRef 连接器：DOI 反查元数据 + 关键词检索（双源并行检索的第二路）。

检索遵循 OpenAlex 连接器的同款性能设计：磁盘缓存（24h TTL）、指数退避重试、
礼貌池标识（mailto）。产物归一到与 openalex._normalize 一致的 schema，
仅用于首轮召回并集；滚雪球扩展保持 OpenAlex 单源（引用图同构）。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

import httpx

from ..config import DATA_DIR
from ..logging_util import log_tool

BASE = "https://api.crossref.org"
MAILTO = "gezhi-scizhi@example.edu"  # 礼貌池标识，部署时替换为团队邮箱
_TIMEOUT = 20.0
_MAX_RETRY = 3
_CACHE_TTL = 24 * 3600
_CACHE_DIR = DATA_DIR / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_client = httpx.Client(timeout=_TIMEOUT,
                       limits=httpx.Limits(max_connections=8,
                                           max_keepalive_connections=4),
                       headers={"User-Agent": "GezhiSciZhi/0.2 (mailto:" + MAILTO + ")"})

_TAG_RE = re.compile(r"<[^>]+>")


def _cache_get(key: str) -> dict[str, Any] | None:
    f = _CACHE_DIR / f"cr_{key}.json"
    if f.exists() and time.time() - f.stat().st_mtime < _CACHE_TTL:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def _cache_put(key: str, data: dict[str, Any]) -> None:
    (_CACHE_DIR / f"cr_{key}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _get(path: str, params: dict[str, Any], node: str) -> dict[str, Any]:
    """带缓存、连接池、重试与日志的 GET 请求（CrossRef 礼貌池）。"""
    params = {**params, "mailto": MAILTO}
    key = hashlib.sha256((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        log_tool(node, "crossref", str(params)[:120], "cache_hit", 0, "success")
        return cached

    t0 = time.perf_counter()
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRY):
        try:
            resp = _client.get(f"{BASE}{path}", params=params)
            if resp.status_code == 429:  # 限流：指数退避
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            _cache_put(key, data)
            log_tool(node, "crossref", str(params)[:120],
                     f"http={resp.status_code}",
                     int((time.perf_counter() - t0) * 1000), "success")
            return data
        except Exception as e:  # noqa: BLE001 —— 网络异常统一重试
            last_err = e
            time.sleep(2 ** attempt)
    log_tool(node, "crossref", str(params)[:120], f"error={last_err}",
             int((time.perf_counter() - t0) * 1000), "failed")
    raise RuntimeError(f"CrossRef 请求失败: {last_err}")


def fetch_by_doi(doi: str, node: str = "citation") -> dict[str, Any] | None:
    """按 DOI 获取元数据，失败返回 None（不阻断主流程）。"""
    try:
        msg = _get(f"/works/{doi}", {}, node)
        return msg.get("message", {})
    except Exception:  # noqa: BLE001 —— 反查失败不阻断
        return None


def _clean_abstract(jats: str) -> str:
    """CrossRef 摘要为 JATS XML 片段：剥标签与数学标记，保留纯文本。"""
    if not jats:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", jats)).strip()


def _normalize(w: dict[str, Any]) -> dict[str, Any]:
    """CrossRef works 条目 → 与 openalex._normalize 一致的统一 schema。"""
    doi = (w.get("DOI") or "").strip()
    authors = []
    for a in w.get("author") or []:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        name = f"{given} {family}".strip() or (a.get("name") or "")
        if name:
            authors.append(name)
    issued = (w.get("issued") or {}).get("date-parts") or [[None]]
    year = issued[0][0] if issued and issued[0] else None
    title = (w.get("title") or [""])[0] or (w.get("original-title") or [""])[0]
    venue = (w.get("container-title") or [""])[0] or ""
    return {
        "id": f"https://doi.org/{doi}" if doi else f"crossref:{w.get('URL', hash(w))}",
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "volume": w.get("volume") or "",
        "issue": w.get("issue") or "",
        "pages": w.get("page") or "",
        "doi": doi,
        "abstract": _clean_abstract(w.get("abstract") or ""),
        "cited_by_count": w.get("is-referenced-by-count", 0),
        "referenced_works": [],  # 引用图归 OpenAlex 单源，滚雪球语义不混 ID 空间
        "url": w.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
        "type": w.get("type", ""),
        "source": "crossref",
    }


def search(query: str, per_page: int = 25, page: int = 1,
           year_from: int | None = None, year_to: int | None = None,
           node: str = "search") -> list[dict[str, Any]]:
    """关键词检索（首轮双源并集的第二路）。失败抛异常，由调用方降级。"""
    params: dict[str, Any] = {"query.bibliographic": query,
                              "rows": max(1, min(per_page, 100)),
                              "offset": (page - 1) * per_page,
                              "select": ",".join([
                                  "DOI", "title", "original-title", "author",
                                  "issued", "container-title", "volume", "issue",
                                  "page", "abstract", "is-referenced-by-count",
                                  "URL", "type"])}
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}-01-01")
    if year_to:
        filters.append(f"until-pub-date:{year_to}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    data = _get("/works", params, node)
    items = (data.get("message") or {}).get("items") or []
    return [_normalize(w) for w in items]
