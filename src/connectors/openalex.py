"""OpenAlex 公开文献库连接器：检索、批量获取、引用/被引扩展（滚雪球）。

性能设计：
- 全局共享 httpx.Client（连接池 + HTTP/1.1 keep-alive），避免每次请求重建连接；
- 磁盘响应缓存（data/cache，TTL 24h），重复检索零网络开销；
- 失败指数退避重试，遵守 OpenAlex 礼貌策略（mailto 标识）。

OpenAlex 为免费开放接口（https://api.openalex.org），无需密钥。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx

from ..config import DATA_DIR
from ..logging_util import log_tool

BASE = "https://api.openalex.org"
MAILTO = "gezhi-scizhi@example.edu"  # 礼貌池标识，部署时替换为团队邮箱
_TIMEOUT = 30.0
_MAX_RETRY = 3
_CACHE_TTL = 24 * 3600
_CACHE_DIR = DATA_DIR / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 连接池：全局单例，keep-alive 复用
_client = httpx.Client(timeout=_TIMEOUT, http2=False,
                       limits=httpx.Limits(max_connections=10,
                                           max_keepalive_connections=5),
                       headers={"User-Agent": "GezhiSciZhi/0.2 (mailto:" + MAILTO + ")"})


def _cache_get(key: str) -> dict[str, Any] | None:
    f = _CACHE_DIR / f"{key}.json"
    if f.exists() and time.time() - f.stat().st_mtime < _CACHE_TTL:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
    return None


def _cache_put(key: str, data: dict[str, Any]) -> None:
    (_CACHE_DIR / f"{key}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _get(path: str, params: dict[str, Any], node: str) -> dict[str, Any]:
    """带缓存、连接池、重试与日志的 GET 请求。"""
    params = {**params, "mailto": MAILTO}
    key = hashlib.sha256((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    cached = _cache_get(key)
    if cached is not None:
        log_tool(node, "openalex", str(params)[:120], "cache_hit", 0, "success")
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
            log_tool(node, "openalex", str(params)[:120],
                     f"http={resp.status_code}", int((time.perf_counter() - t0) * 1000), "success")
            return data
        except Exception as e:  # noqa: BLE001 —— 网络异常统一重试
            last_err = e
            time.sleep(2 ** attempt)
    log_tool(node, "openalex", str(params)[:120], f"error={last_err}",
             int((time.perf_counter() - t0) * 1000), "failed")
    raise RuntimeError(f"OpenAlex 请求失败: {last_err}")


def _reconstruct_abstract(inv_index: dict[str, list[int]] | None) -> str:
    """OpenAlex 摘要以倒排索引存储，需重建为文本。"""
    if not inv_index:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inv_index.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def _normalize(w: dict[str, Any]) -> dict[str, Any]:
    """统一文献结构，全流程只认这个 schema。"""
    loc = (w.get("primary_location") or {})
    src = (loc.get("source") or {})
    biblio = w.get("biblio") or {}
    pages = "-".join(p for p in [biblio.get("first_page"), biblio.get("last_page")] if p)
    return {
        "id": w.get("id", ""),
        "title": w.get("title") or w.get("display_name") or "",
        "authors": [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])],
        "year": w.get("publication_year"),
        "venue": src.get("display_name") or "",
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "pages": pages,
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
        "cited_by_count": w.get("cited_by_count", 0),
        "referenced_works": w.get("referenced_works", []),
        "url": loc.get("landing_page_url") or w.get("id", ""),
        "type": w.get("type", ""),
        "source": "openalex",
    }


def search(query: str, per_page: int = 25, page: int = 1,
           year_from: int | None = None, year_to: int | None = None,
           node: str = "search") -> list[dict[str, Any]]:
    """按关键词检索文献。"""
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    params: dict[str, Any] = {"search": query, "per-page": per_page, "page": page}
    if filters:
        params["filter"] = ",".join(filters)
    data = _get("/works", params, node)
    return [_normalize(w) for w in data.get("results", [])]


def fetch_many(ids: list[str], node: str = "expand") -> list[dict[str, Any]]:
    """按 OpenAlex ID 批量获取文献（滚雪球扩展用）。"""
    if not ids:
        return []
    short = [i.replace("https://openalex.org/", "") for i in ids]
    out: list[dict[str, Any]] = []
    for i in range(0, len(short), 50):  # filter 每批最多 50 个
        chunk = short[i:i + 50]
        data = _get("/works", {"filter": f"ids.openalex:{'|'.join(chunk)}",
                               "per-page": 50}, node)
        out.extend(_normalize(w) for w in data.get("results", []))
    return out


def cited_by(work_id: str, per_page: int = 25, node: str = "expand") -> list[dict[str, Any]]:
    """获取引用了某文献的文献（被引方向滚雪球）。"""
    short = work_id.replace("https://openalex.org/", "")
    data = _get("/works", {"filter": f"cites:{short}", "per-page": per_page,
                           "sort": "cited_by_count:desc"}, node)
    return [_normalize(w) for w in data.get("results", [])]


def cited_by_batch(work_ids: list[str], per_page: int = 10,
                   node: str = "expand") -> list[dict[str, Any]]:
    """并发批量获取多个种子的被引文献（滚雪球提速关键）。"""
    from concurrent.futures import ThreadPoolExecutor
    if not work_ids:
        return []
    with ThreadPoolExecutor(max_workers=min(len(work_ids), 5)) as pool:
        results = list(pool.map(lambda wid: cited_by(wid, per_page, node), work_ids))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch in results:
        for w in batch:
            if w["id"] not in seen:
                seen.add(w["id"])
                out.append(w)
    return out
