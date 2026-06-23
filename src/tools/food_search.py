# -*- coding: utf-8 -*-
import urllib.parse
import httpx
import os
import json
import re
import asyncio
import sys
import subprocess
import time
import contextvars
import hashlib
from pathlib import Path
from src.config.env import load_project_env

load_project_env()

VALID_SEARCH_MODES = {"online_required", "online_first", "offline_only"}
DEFAULT_NODE_DIR = r"E:\tools\nodejs"
SEARCH_TIMELINE = contextvars.ContextVar("food_search_timeline", default=None)
XHS_STORE_NAME_CACHE_PATH = Path("logs/xhs_store_name_cache.json")
XHS_RECIPE_EXTRACT_CACHE_PATH = Path("logs/xhs_recipe_extract_cache.json")
XHS_SOCIAL_SEARCH_CACHE_PATH = Path("logs/xhs_social_search_cache.json")
TAVILY_RECIPE_EXTRACT_CACHE_PATH = Path("logs/tavily_recipe_extract_cache.json")

def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'utf-8'
        sep = kwargs.get('sep', ' ')
        end = kwargs.get('end', '\n')
        msg = sep.join(str(arg) for arg in args) + end
        try:
            sys.stdout.buffer.write(msg.encode(encoding, errors='replace'))
            if kwargs.get('flush', False):
                sys.stdout.flush()
        except Exception:
            # Fallback to standard print in case of write failures
            print(repr(msg))

def _aione_subprocess_env() -> dict:
    env = os.environ.copy()
    env.setdefault("EXECJS_RUNTIME", "Node")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    node_dir = env.get("AIONE_NODE_DIR", DEFAULT_NODE_DIR)
    if node_dir and Path(node_dir).exists():
        path_parts = env.get("PATH", "").split(os.pathsep)
        if node_dir not in path_parts:
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    return env

def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)

def _log_search_event(event: str, **details) -> None:
    timeline = SEARCH_TIMELINE.get()
    if isinstance(timeline, list):
        timeline.append(_timeline_event(event, details))

    log_path = os.getenv("FOOD_SEARCH_TEST_LOG")
    if not log_path:
        return

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        **details,
    }
    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        safe_print(f"【测试日志跳过】{exc}", flush=True)


def _load_xhs_store_name_cache() -> dict:
    try:
        if XHS_STORE_NAME_CACHE_PATH.exists():
            return json.loads(XHS_STORE_NAME_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _save_xhs_store_name_cache(cache: dict) -> None:
    try:
        XHS_STORE_NAME_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        XHS_STORE_NAME_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        _log_search_event("social.xhs.store_name_cache.write_error", error=str(exc)[:300])


def _get_cached_xhs_store_name(note_id: str | None) -> str:
    if not note_id:
        return ""
    cache = _load_xhs_store_name_cache()
    item = cache.get(note_id) or {}
    store_name = item.get("store_name") if isinstance(item, dict) else ""
    return store_name if _looks_like_explicit_store_name(store_name) else ""


def _set_cached_xhs_store_name(note_id: str | None, store_name: str, source: str = "comment") -> None:
    if not note_id or not _looks_like_explicit_store_name(store_name):
        return
    cache = _load_xhs_store_name_cache()
    cache[note_id] = {
        "store_name": store_name,
        "source": source,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _save_xhs_store_name_cache(cache)

def _load_xhs_recipe_extract_cache() -> dict:
    try:
        if XHS_RECIPE_EXTRACT_CACHE_PATH.exists():
            data = json.loads(XHS_RECIPE_EXTRACT_CACHE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _save_xhs_recipe_extract_cache(cache: dict) -> None:
    try:
        XHS_RECIPE_EXTRACT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        XHS_RECIPE_EXTRACT_CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        _log_search_event("social.xhs.recipe_extract_cache.write_error", error=str(exc)[:300])


def _xhs_recipe_extract_cache_key(query: str, detail: dict) -> str:
    note_id = str(detail.get("note_id") or detail.get("safe_url") or detail.get("detail_url") or "")
    title = str(detail.get("title") or "")
    desc = str(detail.get("desc") or "")
    digest = hashlib.sha1(f"{query}\n{note_id}\n{title}\n{desc[:1800]}".encode("utf-8", errors="ignore")).hexdigest()
    return f"{note_id or 'xhs'}:{digest}"


def _get_cached_xhs_recipe_extract(query: str, detail: dict) -> dict | None:
    ttl = int(os.getenv("AIONE_XHS_RECIPE_EXTRACT_CACHE_TTL_SECONDS", "604800") or "0")
    if ttl <= 0:
        return None
    key = _xhs_recipe_extract_cache_key(query, detail)
    item = _load_xhs_recipe_extract_cache().get(key)
    if not isinstance(item, dict):
        return None
    updated_at = float(item.get("updated_at_epoch") or 0)
    if not updated_at or time.time() - updated_at > ttl:
        return None
    recipe = item.get("recipe")
    if isinstance(recipe, dict) and _has_concrete_recipe_fields(recipe):
        _log_search_event(
            "social.xhs.recipe_extract.cache_hit",
            title=detail.get("title") or query,
            cache_hit=True,
        )
        return dict(recipe)
    return None


def _set_cached_xhs_recipe_extract(query: str, detail: dict, recipe: dict) -> None:
    if not isinstance(recipe, dict) or not _has_concrete_recipe_fields(recipe):
        return
    cache = _load_xhs_recipe_extract_cache()
    cache[_xhs_recipe_extract_cache_key(query, detail)] = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "updated_at_epoch": time.time(),
        "note_id": detail.get("note_id"),
        "title": detail.get("title") or query,
        "recipe": recipe,
    }
    max_items = int(os.getenv("AIONE_XHS_RECIPE_EXTRACT_CACHE_MAX_ITEMS", "500") or "500")
    if len(cache) > max_items:
        cache = dict(
            sorted(
                cache.items(),
                key=lambda pair: float((pair[1] or {}).get("updated_at_epoch") or 0),
                reverse=True,
            )[:max_items]
        )
    _save_xhs_recipe_extract_cache(cache)


def _load_tavily_recipe_extract_cache() -> dict:
    try:
        if TAVILY_RECIPE_EXTRACT_CACHE_PATH.exists():
            data = json.loads(TAVILY_RECIPE_EXTRACT_CACHE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _save_tavily_recipe_extract_cache(cache: dict) -> None:
    try:
        TAVILY_RECIPE_EXTRACT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TAVILY_RECIPE_EXTRACT_CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        _log_search_event("tavily.recipe_extract_cache.write_error", error=str(exc)[:300])


def _tavily_recipe_extract_cache_key(query: str, result: dict) -> str:
    url = str(result.get("url") or "")
    title = str(result.get("title") or "")
    content = str(result.get("content") or "")
    digest = hashlib.sha1(f"{query}\n{url}\n{title}\n{content[:2200]}".encode("utf-8", errors="ignore")).hexdigest()
    return f"{url or 'tavily'}:{digest}"


def _get_cached_tavily_recipe_extract(query: str, result: dict) -> dict | None:
    ttl = int(os.getenv("TAVILY_RECIPE_EXTRACT_CACHE_TTL_SECONDS", "604800") or "0")
    if ttl <= 0:
        return None
    item = _load_tavily_recipe_extract_cache().get(_tavily_recipe_extract_cache_key(query, result))
    if not isinstance(item, dict):
        return None
    updated_at = float(item.get("updated_at_epoch") or 0)
    if not updated_at or time.time() - updated_at > ttl:
        return None
    recipe = item.get("recipe")
    if isinstance(recipe, dict) and _has_concrete_recipe_fields(recipe):
        _log_search_event(
            "tavily.recipe_extract.cache_hit",
            title=result.get("title") or query,
            cache_hit=True,
        )
        return dict(recipe)
    return None


def _set_cached_tavily_recipe_extract(query: str, result: dict, recipe: dict) -> None:
    if not isinstance(recipe, dict) or not _has_concrete_recipe_fields(recipe):
        return
    cache = _load_tavily_recipe_extract_cache()
    cache[_tavily_recipe_extract_cache_key(query, result)] = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "updated_at_epoch": time.time(),
        "url": result.get("url"),
        "title": result.get("title") or query,
        "recipe": recipe,
    }
    max_items = int(os.getenv("TAVILY_RECIPE_EXTRACT_CACHE_MAX_ITEMS", "500") or "500")
    if len(cache) > max_items:
        cache = dict(
            sorted(
                cache.items(),
                key=lambda pair: float((pair[1] or {}).get("updated_at_epoch") or 0),
                reverse=True,
            )[:max_items]
        )
    _save_tavily_recipe_extract_cache(cache)


def _load_xhs_social_search_cache() -> dict:
    try:
        if XHS_SOCIAL_SEARCH_CACHE_PATH.exists():
            data = json.loads(XHS_SOCIAL_SEARCH_CACHE_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _save_xhs_social_search_cache(cache: dict) -> None:
    try:
        XHS_SOCIAL_SEARCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        XHS_SOCIAL_SEARCH_CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        _log_search_event("social.xhs.search_cache.write_error", error=str(exc)[:300])


def _xhs_social_search_cache_key(query: str) -> str:
    detail_limit = os.getenv("AIONE_XHS_DETAIL_LIMIT", "3")
    candidate_limit = os.getenv("AIONE_XHS_SEARCH_CANDIDATE_LIMIT", "6")
    digest = hashlib.sha1(f"{query}|{detail_limit}|{candidate_limit}".encode("utf-8", errors="ignore")).hexdigest()
    return digest


def _get_cached_xhs_social_results(query: str) -> list[dict] | None:
    ttl = int(os.getenv("AIONE_XHS_SOCIAL_SEARCH_CACHE_TTL_SECONDS", "1800") or "0")
    if ttl <= 0:
        return None
    item = _load_xhs_social_search_cache().get(_xhs_social_search_cache_key(query))
    if not isinstance(item, dict):
        return None
    updated_at = float(item.get("updated_at_epoch") or 0)
    if not updated_at or time.time() - updated_at > ttl:
        return None
    results = item.get("results")
    if isinstance(results, list):
        if not results:
            _log_search_event("social.xhs.search_cache.empty_ignored", result_count=0, cache_hit=True)
            return None
        _log_search_event("social.xhs.search_cache.hit", result_count=len(results), cache_hit=True)
        return results
    return None


def _set_cached_xhs_social_results(query: str, results: list[dict]) -> None:
    if not isinstance(results, list) or not results:
        return
    cache = _load_xhs_social_search_cache()
    cache[_xhs_social_search_cache_key(query)] = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "updated_at_epoch": time.time(),
        "query": query,
        "results": results,
    }
    max_items = int(os.getenv("AIONE_XHS_SOCIAL_SEARCH_CACHE_MAX_ITEMS", "100") or "100")
    if len(cache) > max_items:
        cache = dict(
            sorted(
                cache.items(),
                key=lambda pair: float((pair[1] or {}).get("updated_at_epoch") or 0),
                reverse=True,
            )[:max_items]
        )
    _save_xhs_social_search_cache(cache)

def _timeline_event(event: str, details: dict) -> dict:
    item = {"event": event}
    for key in [
        "platform",
        "elapsed_ms",
        "result_count",
        "candidate_count",
        "detail_count",
        "returncode",
        "stdout_chars",
        "stdout_preview",
        "stderr_preview",
        "result_source",
        "error_type",
        "error",
        "reason",
        "cache_hit",
        "extract_count",
        "concurrency",
        "selected_image_count",
        "top_image_count",
    ]:
        if key in details:
            item[key] = details[key]
    if "attempt" in details:
        item["attempt"] = details["attempt"]
    if "recipes" in details:
        item["recipes"] = details["recipes"]
    if "titles" in details:
        item["titles"] = details["titles"][:5]
    return item

def _attach_debug_timeline(recipes: list[dict]) -> list[dict]:
    timeline = SEARCH_TIMELINE.get()
    if not isinstance(timeline, list):
        return recipes
    compact = timeline[-80:]
    for recipe in recipes:
        if isinstance(recipe, dict):
            recipe["debug_timeline"] = compact
    return recipes

def _recipe_log_summary(recipes: list[dict]) -> list[dict]:
    summary = []
    for recipe in recipes:
        if not isinstance(recipe, dict):
            continue
        summary.append({
            "name": recipe.get("name"),
            "source": recipe.get("source"),
            "source_url": recipe.get("source_url"),
            "image_url": recipe.get("image_url"),
            "source_image_urls": recipe.get("source_image_urls"),
            "description": recipe.get("description"),
            "step_count": len(recipe.get("steps") or []),
        })
    return summary

def _is_relevant_social_title(title: str, query: str) -> bool:
    synonym_groups = [
        {"番茄", "西红柿", "蕃茄"},
        {"鸡蛋", "蛋", "炒蛋"},
    ]
    if query and query in title:
        return True
    for group in synonym_groups:
        if any(word in query for word in group) and any(word in title for word in group):
            continue
        if any(word in query for word in group):
            return False
    query_chars = {char for char in query if "\u4e00" <= char <= "\u9fff"}
    if not query_chars:
        return query.lower() in title.lower()
    return len(query_chars.intersection(title)) >= min(2, len(query_chars))

def _summarize_aione_payload(platform: str, payload: str, query: str, limit: int = 8) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload[:1200]

    if not (isinstance(data, list) and len(data) >= 3 and data[0] is True):
        return ""

    body = data[2]
    if platform == "douyin":
        return _summarize_douyin_payload(body, query, limit)

    if platform == "xhs" and isinstance(body, dict):
        items = body.get("data", {}).get("items", [])
        lines = []
        for item in items[:limit]:
            card = item.get("note_card", {}) if isinstance(item, dict) else {}
            user_info = card.get("user", {})
            interact = card.get("interact_info", {})
            title = card.get("display_title") or card.get("title") or ""
            user = user_info.get("nickname") or user_info.get("nick_name") or ""
            liked = interact.get("liked_count", "")
            collected = interact.get("collected_count", "")
            if title and _is_relevant_social_title(title, query):
                lines.append(f"- {title} | 作者: {user} | 点赞: {liked} | 收藏: {collected}")
        if lines:
            return "\n".join(lines)
        return ""

    return json.dumps(body, ensure_ascii=False)[:1200]

def _summarize_douyin_payload(body, query: str, limit: int = 8) -> str:
    items = body if isinstance(body, list) else body.get("data", []) if isinstance(body, dict) else []
    lines = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        aweme = item.get("aweme_info") or item.get("aweme") or item
        title = aweme.get("desc") or aweme.get("caption") or aweme.get("title") or ""
        author = (aweme.get("author") or {}).get("nickname", "")
        statistics = aweme.get("statistics") or {}
        cover = (
            aweme.get("video", {})
            .get("cover", {})
            .get("url_list", [])
        )
        if title and _is_relevant_social_title(title, query):
            lines.append(
                f"- 标题: {title[:120]} | 作者: {author} | 点赞: {statistics.get('digg_count', '')} | 评论: {statistics.get('comment_count', '')} | 封面: {cover[0] if cover else ''}"
            )
    return "\n".join(lines)

def _extract_xhs_search_candidates(payload: str, query: str, limit: int = 6) -> list[dict]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []

    if not (isinstance(data, list) and len(data) >= 3 and data[0] is True):
        return []

    items = data[2].get("data", {}).get("items", []) if isinstance(data[2], dict) else []
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        card = item.get("note_card", {})
        title = card.get("display_title") or card.get("title") or ""
        if not title or not _is_relevant_social_title(title, query):
            continue
        user_info = card.get("user", {})
        interact = card.get("interact_info", {})
        candidates.append({
            "note_id": item.get("id") or card.get("note_id"),
            "xsec_token": item.get("xsec_token"),
            "title": title,
            "author": user_info.get("nickname") or user_info.get("nick_name") or "",
            "liked_count": interact.get("liked_count", ""),
            "collected_count": interact.get("collected_count", ""),
            "comment_count": interact.get("comment_count", ""),
        })
        if len(candidates) >= limit:
            break
    return candidates

def _xhs_note_url(candidate: dict) -> str | None:
    note_id = candidate.get("note_id")
    token = candidate.get("xsec_token")
    if not note_id or not token:
        return None
    return (
        f"https://www.xiaohongshu.com/explore/{note_id}"
        f"?xsec_token={urllib.parse.quote(token)}&xsec_source=pc_search"
    )

def _safe_xhs_note_url(candidate: dict) -> str | None:
    note_id = candidate.get("note_id")
    if not note_id:
        return None
    return f"https://www.xiaohongshu.com/explore/{note_id}"

def _summarize_xhs_detail_payload(payload: str) -> dict | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not (isinstance(data, list) and len(data) >= 3 and data[0] is True):
        return None

    body = data[2]
    items = body.get("data", {}).get("items", []) if isinstance(body, dict) else []
    if not items:
        return None

    card = items[0].get("note_card", {}) if isinstance(items[0], dict) else {}
    interact = card.get("interact_info", {})
    user_info = card.get("user", {})
    desc = card.get("desc") or card.get("description") or ""
    title = card.get("title") or card.get("display_title") or ""
    image_urls = _extract_xhs_image_urls(card)
    return {
        "note_id": card.get("note_id") or items[0].get("id"),
        "title": title,
        "author": user_info.get("nickname") or user_info.get("nick_name") or "",
        "desc": desc[:2000],
        "image_urls": image_urls,
        "liked_count": interact.get("liked_count", ""),
        "collected_count": interact.get("collected_count", ""),
        "comment_count": interact.get("comment_count", ""),
    }

def _extract_xhs_image_urls(card: dict, limit: int = 6) -> list[str]:
    urls = []
    cover = card.get("cover") or {}
    for key in ["url_default", "url_pre", "url"]:
        if cover.get(key):
            urls.append(cover[key])
    for image in card.get("image_list") or []:
        if not isinstance(image, dict):
            continue
        for key in ["url_default", "url_pre", "url"]:
            if image.get(key):
                urls.append(image[key])
        for info in image.get("info_list") or []:
            if isinstance(info, dict) and info.get("url"):
                urls.append(info["url"])
    unique = []
    for url in urls:
        if url and url not in unique:
            unique.append(url)
        if len(unique) >= limit:
            break
    return unique

def _clean_social_text(text: str, fallback: str = "", limit: int = 120) -> str:
    text = text or ""
    text = re.sub(r"#[^#\n\r]{1,40}\[话题\]#", " ", text)
    text = re.sub(r"#[^#\n\r]{1,40}#", " ", text)
    text = re.sub(r"\[[^\]]{1,12}\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。；;、\n\r\t")
    if not text:
        text = fallback or "来自小红书的真实图文菜谱，点击卡片查看做法摘要。"
    if len(text) <= limit:
        return text
    sentence_end = max(text.rfind("。", 0, limit), text.rfind("！", 0, limit), text.rfind("!", 0, limit))
    if sentence_end >= 24:
        return text[:sentence_end + 1]
    return text[:limit].rstrip(" ，,。；;、") + "..."

async def _fetch_xhs_note_details(cli_path: str, candidates: list[dict], detail_limit: int | None = None) -> list[dict]:
    detail_limit = detail_limit or int(os.getenv("AIONE_XHS_DETAIL_LIMIT", "3"))
    timeout = float(os.getenv("AIONE_TIMEOUT_SECONDS", "20"))
    details = []
    for candidate in candidates[:detail_limit]:
        detail_url = _xhs_note_url(candidate)
        safe_url = _safe_xhs_note_url(candidate)
        if not detail_url:
            continue

        started = time.perf_counter()
        command = [cli_path, "xhs", "note", "info", "--url", detail_url, "--output", "json"]
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=os.getcwd(),
                env=_aione_subprocess_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            _log_search_event(
                "social.xhs.detail.response",
                elapsed_ms=_elapsed_ms(started),
                note_id=candidate.get("note_id"),
                title=candidate.get("title"),
                safe_url=safe_url,
                returncode=completed.returncode,
                stdout_chars=len(completed.stdout or ""),
                stderr_preview=(completed.stderr or "")[:300],
            )
            if completed.returncode != 0:
                continue

            detail = _summarize_xhs_detail_payload(completed.stdout)
            if not detail:
                continue
            detail["safe_url"] = safe_url
            detail["detail_url"] = detail_url
            details.append(detail)
            _log_search_event(
                "social.xhs.detail.extracted",
                elapsed_ms=_elapsed_ms(started),
                note_id=detail.get("note_id"),
                title=detail.get("title"),
                author=detail.get("author"),
                desc_preview=(detail.get("desc") or "")[:700],
                image_urls=detail.get("image_urls") or [],
                liked_count=detail.get("liked_count"),
                collected_count=detail.get("collected_count"),
                comment_count=detail.get("comment_count"),
            )
        except Exception as exc:
            _log_search_event(
                "social.xhs.detail.error",
                elapsed_ms=_elapsed_ms(started),
                note_id=candidate.get("note_id"),
                title=candidate.get("title"),
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
    return details

async def probe_xhs_available(query: str, limit: int = 3) -> dict:
    """Lightweight XHS cookie/CLI probe. Search success with candidates means usable."""
    started = time.perf_counter()
    SEARCH_TIMELINE.set([])
    if os.getenv("AIONE_ENABLED", "0") != "1":
        _log_search_event("xhs_probe.done", elapsed_ms=_elapsed_ms(started), available=False, reason="aione_disabled")
        return {"available": False, "candidate_count": 0, "reason": "aione_disabled", "debug_timeline": SEARCH_TIMELINE.get()}

    cli_path = os.getenv("AIONE_CLI_PATH", "aione")
    if cli_path.endswith(".exe") and not Path(cli_path).exists():
        _log_search_event("xhs_probe.done", elapsed_ms=_elapsed_ms(started), available=False, reason="cli_missing")
        return {"available": False, "candidate_count": 0, "reason": "cli_missing", "debug_timeline": SEARCH_TIMELINE.get()}

    timeout = float(os.getenv("AIONE_XHS_PROBE_TIMEOUT_SECONDS", os.getenv("AIONE_TIMEOUT_SECONDS", "6")) or "6")
    command = [cli_path, "xhs", "note", "search", "--query", query or "家常菜", "--page", "1", "--output", "json"]
    _log_search_event("xhs_probe.start", query=query, timeout_seconds=timeout)
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=os.getcwd(),
            env=_aione_subprocess_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _log_search_event("xhs_probe.timeout", elapsed_ms=_elapsed_ms(started), query=query, timeout_seconds=timeout)
        return {"available": False, "candidate_count": 0, "reason": "timeout", "debug_timeline": SEARCH_TIMELINE.get()}
    except Exception as exc:
        _log_search_event("xhs_probe.done", elapsed_ms=_elapsed_ms(started), available=False, reason=type(exc).__name__, error=str(exc)[:300])
        return {"available": False, "candidate_count": 0, "reason": type(exc).__name__, "debug_timeline": SEARCH_TIMELINE.get()}

    candidates = _extract_xhs_search_candidates(completed.stdout or "", query or "家常菜", limit=limit)
    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""
    available = completed.returncode == 0 and bool(candidates)
    _log_search_event(
        "xhs_probe.done",
        elapsed_ms=_elapsed_ms(started),
        available=available,
        returncode=completed.returncode,
        candidate_count=len(candidates),
        stdout_preview=stdout_text[:800],
        stderr_preview=stderr_text[:500],
    )
    return {
        "available": available,
        "candidate_count": len(candidates),
        "reason": "ok" if available else "no_candidates",
        "debug_timeline": SEARCH_TIMELINE.get(),
    }


def _walk_json_values(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_values(child)


def _extract_comment_texts(payload: str, author: str = "") -> list[str]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return []
    texts = []
    author = (author or "").strip()
    for node in _walk_json_values(data):
        if not isinstance(node, dict):
            continue
        text = (
            node.get("content")
            or node.get("text")
            or node.get("comment")
            or node.get("desc")
            or node.get("note")
            or ""
        )
        if not isinstance(text, str) or len(text.strip()) < 2:
            continue
        user = node.get("user_info") or node.get("user") or {}
        nickname = ""
        if isinstance(user, dict):
            nickname = user.get("nickname") or user.get("nick_name") or user.get("name") or ""
        is_author = bool(node.get("is_author") or node.get("is_note_author") or node.get("is_owner"))
        priority = 0
        if is_author:
            priority += 2
        if author and nickname and nickname == author:
            priority += 2
        if any(word in text for word in ["店名", "店叫", "叫", "地址", "位置", "在", "店铺"]):
            priority += 1
        texts.append((priority, text.strip()))
    texts.sort(key=lambda item: item[0], reverse=True)
    unique = []
    for _, text in texts:
        if text not in unique:
            unique.append(text)
    return unique[:30]


async def _fetch_xhs_store_name_from_comments(cli_path: str, detail: dict) -> str:
    note_id = detail.get("note_id")
    cached = _get_cached_xhs_store_name(note_id)
    if cached:
        _log_search_event("social.xhs.comment_store_name.cache_hit", note_id=note_id, store_name=cached)
        return cached

    detail_url = detail.get("detail_url") or detail.get("safe_url")
    if not detail_url:
        return ""
    started = time.perf_counter()
    timeout = float(os.getenv("AIONE_XHS_COMMENT_TIMEOUT_SECONDS", os.getenv("AIONE_TIMEOUT_SECONDS", "20")))
    command = [cli_path, "xhs", "note", "all-comment", "--url", detail_url, "--output", "json"]
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=os.getcwd(),
            env=_aione_subprocess_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        _log_search_event(
            "social.xhs.comment.response",
            elapsed_ms=_elapsed_ms(started),
            note_id=note_id,
            returncode=completed.returncode,
            stdout_chars=len(completed.stdout or ""),
            stderr_preview=(completed.stderr or "")[:300],
        )
        if completed.returncode != 0:
            return ""
        for text in _extract_comment_texts(completed.stdout, detail.get("author", "")):
            store_name = _extract_store_name("", "", text)
            if store_name and store_name != "待从笔记确认店名":
                _set_cached_xhs_store_name(note_id, store_name, source="comment")
                _log_search_event("social.xhs.comment_store_name.extracted", note_id=note_id, store_name=store_name)
                return store_name
    except Exception as exc:
        _log_search_event(
            "social.xhs.comment.error",
            elapsed_ms=_elapsed_ms(started),
            note_id=note_id,
            error_type=type(exc).__name__,
            error=str(exc)[:400],
        )
    return ""

def _format_xhs_details(details: list[dict]) -> str:
    lines = []
    for detail in details:
        desc = (detail.get("desc") or "").replace("\r", "\n").strip()
        image_urls = detail.get("image_urls") or []
        lines.append(
            "\n".join([
                f"- 标题: {detail.get('title')}",
                f"  作者: {detail.get('author')} | 点赞: {detail.get('liked_count')} | 收藏: {detail.get('collected_count')} | 评论: {detail.get('comment_count')}",
                f"  链接: {detail.get('safe_url')}",
                f"  图片: {', '.join(image_urls[:3])}",
                f"  正文: {desc[:1200]}",
            ])
        )
    return "\n".join(lines)

async def _search_xhs_with_tavily(query: str) -> dict | None:
    if os.getenv("AIONE_XHS_TAVILY_FALLBACK", "1") != "1":
        return None

    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key or tavily_key == "your_tavily_api_key_here":
        return None

    started = time.perf_counter()
    targeted_query = f'site:xiaohongshu.com "{query}" 小红书 美食 做法'
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": targeted_query,
                    "max_results": int(os.getenv("AIONE_XHS_TAVILY_MAX_RESULTS", "5")),
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                },
                timeout=float(os.getenv("TAVILY_TIMEOUT_SECONDS", "12")),
            )
        if resp.status_code != 200:
            _log_search_event(
                "social.xhs_tavily.http_error",
                elapsed_ms=_elapsed_ms(started),
                status_code=resp.status_code,
                response_preview=resp.text[:300],
            )
            return None

        data = resp.json()
        results = data.get("results", [])
        relevant = [
            r for r in results
            if _is_relevant_social_title(r.get("title") or "", query)
        ]
        _log_search_event(
            "social.xhs_tavily.response",
            elapsed_ms=_elapsed_ms(started),
            result_count=len(results),
            relevant_count=len(relevant),
            titles=[r.get("title") for r in results],
        )
        if not relevant:
            return None

        lines = [
            f"- {r.get('title')} | 来源: {r.get('url')} | 摘要: {(r.get('content') or '')[:160]}"
            for r in relevant
        ]
        return {
            "platform": "xhs_tavily",
            "command": targeted_query,
            "summary": "\n".join(lines),
        }
    except Exception as exc:
        _log_search_event(
            "social.xhs_tavily.error",
            elapsed_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        return None

# 本地美食与餐厅数据库（Mock 备用数据，用于在没有网络或真实 API 时提供稳定饱满的返回）
TRENDING_FOODS = [
    {"name": "蒜蓉小龙虾", "category": "夜市热点", "season": "夏季", "rating": 4.8},
    {"name": "冰镇酸梅汤", "category": "消暑甜品", "season": "夏季", "rating": 4.9},
    {"name": "羊肉炉", "category": "滋补火锅", "season": "冬季", "rating": 4.7},
    {"name": "大闸蟹", "category": "时令海鲜", "season": "秋季", "rating": 4.9},
    {"name": "椰子鸡", "category": "清淡养生", "season": "四季", "rating": 4.6},
]

MOCK_RECIPES = {
    "番茄炒蛋": {
        "name": "经典番茄炒蛋",
        "category": "家常菜",
        "source": "下厨房",
        "image_url": "/images/classic_tomato_egg.png",
        "description": "酸甜可口，营养丰富，超级下饭的国民家常菜。",
        "ingredients": ["西红柿 2个", "鸡蛋 3个"],
        "condiments": ["油 15ml", "食白糖 5g", "食盐 3g", "小葱 1根"],
        "steps": [
            "【新鲜度处理】检查鸡蛋壳是否干净完整，番茄外表红润无软烂，说明食材新鲜。",
            "【物态判断-热锅】大火烧热空锅，待锅底微微浮现【一层薄薄白烟】时，转中火，倒入冷油并撒入极少许【食盐】（物理防粘）。",
            "【炒蛋】倒入打散的蛋液。蛋液边缘遇热油迅速【膨胀起泡】时，用筷子快速划散至金黄，立即盛出（保持鲜嫩）。",
            "【炒番茄】锅中余油，倒入切好块的番茄大火翻炒，加入白糖，待番茄汁炒出【浓稠浆糊状】时，倒入鸡蛋合炒。",
            "【出锅】翻炒均匀，撒入葱花，起锅装盘。"
        ],
        "calories": 250
    },
    "白切鸡": {
        "name": "正宗广式白切鸡",
        "category": "粤菜",
        "source": "小红书",
        "image_url": "/images/steamed_duck.png",
        "description": "皮脆肉嫩，原汁原味，佐以姜葱茸，鲜美无比。",
        "ingredients": ["三黄鸡 半只", "小葱 3根", "生姜 1块"],
        "condiments": ["花生油 20ml", "食盐 5g", "料酒 10ml"],
        "steps": [
            "【去腥处理-新鲜度判断】检查鸡肉，若鸡皮光滑且无异味说明新鲜，仅需冷水焯水；若为冷冻鸡，焯水时必须加入【葱姜和料酒】以彻底除腥。",
            "【物态判断-三提三浸】大锅烧水，水温达到【即将沸腾但未大滚】（锅底有细密小气泡不断升起）时，提鸡头，将鸡身浸入水中5秒，提起沥水。重复三次，以使鸡皮遇热迅速收紧定型。",
            "【小火慢炖】将鸡浸入水中，盖上锅盖，转微火（保持水面【微沸不滚】，仅有少量气泡浮出），慢炖 20 分钟。",
            "【过冷水】捞出鸡肉，立刻投入冰水中浸泡10分钟（使鸡皮与皮下脂肪遇冷凝结成爽脆的皮冻）。",
            "【斩件】沥干水分，斩件摆盘，佐以热花生油淋熟的姜葱茸蘸料。"
        ],
        "calories": 380
    },
    "清蒸鸭肉": {
        "name": "清蒸鸭肉",
        "category": "清淡调养菜",
        "source": "本地健康替代菜谱",
        "image_url": "/images/steamed_duck.png",
        "description": "口味清淡，适合作为温热性食材被拦截后的安全替代硬菜。",
        "ingredients": ["鸭肉 300g", "生姜 3片", "小葱 2根"],
        "condiments": ["食盐 3g", "料酒 10ml", "清水 适量"],
        "steps": [
            "【新鲜度处理】观察鸭肉表面应有光泽且无异味；若为冷冻鸭肉，先自然解冻并冲洗血水。",
            "【去腥处理】冷水下锅，加入姜片、葱段和料酒，水面出现灰白浮沫后撇净，捞出沥干。",
            "【物态判断-清蒸】蒸锅上汽后放入鸭肉，中火蒸 25 分钟，筷子能轻松扎透且流出清亮肉汁时即可。"
        ],
        "calories": 320
    },
    "香菇菜心": {
        "name": "香菇蚝油菜心",
        "category": "家常菜",
        "source": "下厨房",
        "image_url": "/images/steamed_duck.png",
        "description": "翠绿爽口，香菇滑嫩，蚝油香浓。",
        "ingredients": ["菜心 300g", "鲜香菇 6个"],
        "condiments": ["蚝油 15ml", "大蒜 3瓣", "生粉 5g", "油 10ml"],
        "steps": [
            "【焯水物态】大锅烧水，水开后加入 1g 盐 and 3ml 油（保持菜心翠绿）。放入菜心焯水 1 分钟，待叶子【变深绿变软】立刻捞出摆盘。",
            "【炒香菇】热锅凉油，下蒜末爆香，放入切片的香菇大火翻炒至【微软出水】。",
            "【蚝油汁】加入蚝油、生抽 and 少量水煮开，淋入水淀粉，待芡汁【起泡变浓稠】时，淋在菜心上。"
        ],
        "calories": 120
    },
    "柠檬鸡爪": {
        "name": "酸辣柠檬鸡爪",
        "category": "凉菜/小吃",
        "source": "小红书",
        "image_url": "/images/cantonese_dim_sum.png",
        "description": "酸辣开胃，爽脆Q弹，红遍小红书的追剧必备小零食。",
        "ingredients": ["鸡爪 500g", "柠檬 1个"],
        "condiments": ["小米辣 5个", "大蒜 1头", "生抽 50ml", "香醋 30ml", "白糖 10g", "香菜 2根", "生姜 1块", "料酒 15ml"],
        "steps": [
            "【去腥处理-新鲜度判断】冷水入锅放入鸡爪，必须加入【葱结、姜片 and 料酒】以彻底去除禽腥味，大火烧开转中火煮 10 分钟。",
            "【锁脆物态判断】将锁脆物态放入冰水中浸凉，直到鸡爪表皮收紧变硬、质感爽脆。",
            "【调味腌制】大碗中放入去籽切片的柠檬（去籽防止发苦）、蒜末、小米辣圈、生抽、香醋、糖 and 香菜段，放入鸡爪拌匀，置于冰箱冷藏腌制 4 小时以上。"
        ],
        "calories": 180
    },
    "培根虾滑": {
        "name": "芝士培根虾滑卷",
        "category": "创意料理",
        "source": "小红书",
        "image_url": "/images/classic_tomato_egg.png",
        "description": "空气炸锅懒人菜，焦香培根包裹鲜美虾滑，芝士爆浆，风味绝佳。",
        "ingredients": ["培根 4片", "青虾仁/虾泥 150g", "马苏里拉芝士 30g"],
        "condiments": ["黑胡椒粉 2g", "料酒 5ml", "食用油 5ml"],
        "steps": [
            "【新鲜度处理】虾泥中加入料酒 and 黑胡椒粉，顺时针搅拌至【拉丝上劲】说明新鲜度高、粘性好。",
            "【物态判断-烤制】空气炸锅喷少许油，放入卷好的培根卷，180度烤10分钟，翻面再烤5分钟，至培根【金黄焦脆、芝士融化爆浆】时即可出锅。"
        ],
        "calories": 280
    }
}

MOCK_RESTAURANTS = [
    {
        "id": "r_01",
        "name": "五道口老川鲁火锅",
        "cuisine": "川菜/火锅",
        "source": "美团美食",
        "image_url": "/images/old_chuan_lu_hotpot.png",
        "location": "海淀区五道口商业街3号",
        "rating": 4.7,
        "avg_price": 95,
        "signature_dishes": ["冷锅鲜毛肚", "麻辣九宫格", "现炸酥肉", "精酿啤酒"],
        "reviews": {
            "good": "毛肚非常爽脆，味道很正宗，分量挺足！",
            "bad": "环境有点吵，晚上排队等了一个多小时，体验一般。"
        },
        "platforms": ["大众点评 4.7分", "美团 4.6分", "抖音美食 4.8分"]
    },
    {
        "id": "r_02",
        "name": "知春里广式点心局",
        "cuisine": "粤菜/点心",
        "source": "大众点评",
        "image_url": "/images/cantonese_dim_sum.png",
        "location": "海淀区知春路地铁A口旁",
        "rating": 4.8,
        "avg_price": 85,
        "signature_dishes": ["招招水晶虾饺", "金牌红米肠", "杨枝甘露"],
        "reviews": {
            "good": "虾饺里有整颗虾仁，很Q弹！服务态度特别好。",
            "bad": "店面比较小，周末中午来排不上队，甜点偏甜了点。"
        },
        "platforms": ["大众点评 4.8分", "美团 4.8分"]
    }
]

def get_trending_foods(season: str = "夏季") -> list[dict]:
    """获取当前时令火热的美食排行榜"""
    return [food for food in TRENDING_FOODS if food["season"] == season or food["season"] == "四季"]

def clean_query(q: str) -> str:
    """提取搜索长难句中的核心食物/菜系名词，清洗掉口语化和语气词"""
    stop_words = ["我想吃", "怎么做", "推荐", "有什么", "什么菜", "做法", "怎么", "想吃", "有没有", "来一个", "来份", "来个", "做一个", "做个"]
    cleaned = q
    for word in stop_words:
        cleaned = cleaned.replace(word, "")
    # 过滤标点符号
    cleaned = re.sub(r"[？\?！!，,。\.“”\"'（）\(\)\[\]【】\s\-\_]", "", cleaned)
    return cleaned if cleaned else q


def clean_recipe_image_query(q: str) -> str:
    """Keep only the dish-name part for image relevance checks."""
    text = clean_query(q or "")
    noisy_words = [
        "家常", "正宗", "详细配料", "配料", "食材", "调料", "步骤", "火候",
        "菜谱", "成品图", "图片", "美食", "教程", "下厨房", "小红书", "抖音",
    ]
    for word in noisy_words:
        text = text.replace(word, "")
    return text.strip() or clean_query(q or "")

def _safe_int(value, default: int = 0) -> int:
    try:
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            return int(match.group(0)) if match else default
        return int(value)
    except Exception:
        return default

async def search_recipes_online(query: str, rec_count: int = 3, exclude_names: list[str] = None) -> list[dict]:
    """
    在线搜索菜谱信息。
    支持生成指定数量的候选食谱，并能排除特定菜品名（以支持换一换与大范围挑选）。
    """
    SEARCH_TIMELINE.set([])
    overall_started = time.perf_counter()
    mode = os.getenv("FOOD_SEARCH_MODE", "online_required").strip() or "online_required"
    if mode not in VALID_SEARCH_MODES:
        raise ValueError(f"Unsupported FOOD_SEARCH_MODE: {mode}. Expected one of {sorted(VALID_SEARCH_MODES)}")

    if mode == "offline_only":
        return _attach_debug_timeline(_search_recipes_from_local_cache(query, rec_count, exclude_names))

    tavily_key = os.getenv("TAVILY_API_KEY")
    llm_key = os.getenv("LLM_API_KEY")
    llm_base = os.getenv("LLM_API_BASE", "https://api.bianxie.ai/v1")
    llm_model = os.getenv("LLM_MODEL", "deepseek-chat")
    home_recipe_source_mode = os.getenv("HOME_RECIPE_SOURCE_MODE", "tavily_only").strip() or "tavily_only"

    if not llm_key or llm_key == "your_llm_api_key_here":
        raise ValueError("【LLM 错误】未配置有效 LLM_API_KEY。由于已关闭全部降级处理，系统直接抛出错误。")

    cleaned_query = clean_query(query)
    search_query = f"{cleaned_query} 正宗家常做法 详细配料 步骤 火候 小红书 下厨房 抖音 美食"

    _log_search_event(
        "search.start",
        query=query,
        cleaned_query=cleaned_query,
        rec_count=rec_count,
        mode=mode,
        home_recipe_source_mode=home_recipe_source_mode,
        aione_enabled=os.getenv("AIONE_ENABLED", "0") == "1",
    )

    search_context = ""
    tavily_results = []
    social_results = []
    
    retries = int(os.getenv("FOOD_SEARCH_RETRIES", "2"))
    max_tavily_results = max(6, rec_count + (len(exclude_names) if exclude_names else 0))

    if home_recipe_source_mode == "tavily_only":
        _log_search_event(
            "social.skipped",
            reason="HOME_RECIPE_SOURCE_MODE=tavily_only",
            platform="xhs",
        )
        social_fast_recipes = []
    else:
        social_started = time.perf_counter()
        social_results_from_cache = False
        social_results = _get_cached_xhs_social_results(cleaned_query)
        if social_results is None:
            social_results = await _search_social_sources(cleaned_query)
            _set_cached_xhs_social_results(cleaned_query, social_results)
        else:
            social_results_from_cache = True
        _log_search_event(
            "social.complete",
            elapsed_ms=_elapsed_ms(social_started),
            result_count=len(social_results),
            platforms=[r.get("platform") for r in social_results],
        )
        social_fast_recipes = await _recipes_from_social_results(cleaned_query, social_results, rec_count)
        if (
            not social_fast_recipes
            and social_results_from_cache
            and os.getenv("AIONE_XHS_REFRESH_WEAK_CACHE", "1") == "1"
        ):
            _log_search_event(
                "social.xhs.search_cache.weak_refresh",
                reason="cached_results_did_not_extract_recipe",
                result_count=len(social_results),
            )
            social_started = time.perf_counter()
            social_results = await _search_social_sources(cleaned_query)
            _set_cached_xhs_social_results(cleaned_query, social_results)
            _log_search_event(
                "social.complete",
                elapsed_ms=_elapsed_ms(social_started),
                result_count=len(social_results),
                platforms=[r.get("platform") for r in social_results],
                cache_hit=False,
            )
            social_fast_recipes = await _recipes_from_social_results(cleaned_query, social_results, rec_count)
    if (
        social_fast_recipes
        and os.getenv("FOOD_SEARCH_SOCIAL_FAST_RETURN", "1") == "1"
        and len(social_fast_recipes) >= rec_count
    ):
        _log_search_event(
            "search.final",
            elapsed_ms=_elapsed_ms(overall_started),
            result_source="social_details_fast",
            result_count=len(social_fast_recipes),
            recipes=_recipe_log_summary(social_fast_recipes),
        )
        return _attach_debug_timeline(social_fast_recipes)

    if not tavily_key or tavily_key == "your_tavily_api_key_here":
        if social_fast_recipes:
            _log_search_event(
                "search.final",
                elapsed_ms=_elapsed_ms(overall_started),
                result_source="social_details",
                result_count=len(social_fast_recipes),
                recipes=_recipe_log_summary(social_fast_recipes),
            )
            return _attach_debug_timeline(social_fast_recipes)
        raise ValueError("【Tavily 错误】未配置有效 TAVILY_API_KEY，且小红书详情不足以生成结果。")

    safe_print("\n" + "="*80, flush=True)
    safe_print(f"【MCP 真实调用】正在发起 Tavily 联网检索...", flush=True)
    safe_print(f"  查询词: '{search_query}'", flush=True)
    safe_print("="*80 + "\n", flush=True)

    for attempt in range(retries):
        tavily_started = time.perf_counter()
        _log_search_event(
            "tavily.attempt.start",
            attempt=attempt + 1,
            max_results=max_tavily_results,
            query=search_query,
        )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": tavily_key,
                        "query": search_query,
                        "max_results": max_tavily_results,
                        "search_depth": "advanced",
                        "include_answer": False,
                        "include_raw_content": False,
                        "include_images": os.getenv("TAVILY_INCLUDE_IMAGES", "1") == "1",
                        "include_image_descriptions": os.getenv("TAVILY_INCLUDE_IMAGE_DESCRIPTIONS", "1") == "1",
                    },
                    timeout=float(os.getenv("TAVILY_TIMEOUT_SECONDS", "12"))
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tavily_results = data.get("results", [])
                    tavily_images = data.get("images", [])
                    if tavily_images:
                        for item in tavily_results:
                            if isinstance(item, dict) and not item.get("images"):
                                item["images"] = tavily_images
                    _log_search_event(
                        "tavily.attempt.success",
                        attempt=attempt + 1,
                        elapsed_ms=_elapsed_ms(tavily_started),
                        result_count=len(tavily_results),
                        image_count=len(tavily_images) if isinstance(tavily_images, list) else 0,
                        results=[
                            {
                                "title": r.get("title"),
                                "url": r.get("url"),
                                "content_preview": (r.get("content") or "")[:500],
                                "content_length": len(r.get("content") or ""),
                                "image_count": len(r.get("images") or []) if isinstance(r.get("images"), list) else 0,
                            }
                            for r in tavily_results
                        ],
                    )
                    break
                else:
                    _log_search_event(
                        "tavily.attempt.http_error",
                        attempt=attempt + 1,
                        elapsed_ms=_elapsed_ms(tavily_started),
                        status_code=resp.status_code,
                        response_preview=resp.text[:300],
                    )
                    raise RuntimeError(f"API 返回错误状态码: {resp.status_code}，响应: {resp.text}")
        except Exception as e:
            _log_search_event(
                "tavily.attempt.error",
                attempt=attempt + 1,
                elapsed_ms=_elapsed_ms(tavily_started),
                error_type=type(e).__name__,
                error=str(e)[:500],
            )
            wait_time = 1.0 * (attempt + 1)
            safe_print(f"【Tavily 尝试 {attempt+1}/{retries} 失败】({e})。正在重试...", flush=True)
            if attempt < retries - 1:
                await asyncio.sleep(wait_time)
            else:
                if mode == "online_first" and os.getenv("FOOD_SEARCH_ALLOW_FALLBACK", "0") == "1":
                    safe_print("【Tavily 降级】联网检索失败，显式允许 fallback，改用本地缓存。", flush=True)
                    local_recipes = _search_recipes_from_local_cache(query, rec_count, exclude_names)
                    _log_search_event(
                        "search.final",
                        elapsed_ms=_elapsed_ms(overall_started),
                        result_source="local_cache",
                        result_count=len(local_recipes),
                        recipes=_recipe_log_summary(local_recipes),
                    )
                    return _attach_debug_timeline(local_recipes)
                raise RuntimeError(f"【Tavily 最终失败】API 调用异常: {e}")

    if tavily_results or social_results:
        safe_print("\n" + "="*80, flush=True)
        safe_print(f"【Tavily 搜索成功】共获取到 {len(tavily_results)} 条多源参考网页数据：", flush=True)
        for idx, r in enumerate(tavily_results):
            safe_print(f"  {idx+1}. 标题: {r.get('title')}", flush=True)
            safe_print(f"     链接: {r.get('url')}", flush=True)
            safe_print(f"     摘要: {r.get('content')[:120]}...", flush=True)
        safe_print("="*80 + "\n", flush=True)
        
        # 拼接上下文，限制长度以降低 LLM 超时概率
        tavily_context = "\n\n".join([
            f"网页标题: {r.get('title')}\n来源URL: {r.get('url')}\n网页摘要: {(r.get('content') or '')[:600]}"
            for r in tavily_results[: int(os.getenv("FOOD_SEARCH_CONTEXT_RESULTS", "4"))]
        ])
        social_context = "\n\n".join([
            f"社交平台: {r['platform']}\n命令: {r['command']}\n结果摘要: {r['summary']}"
            for r in social_results
        ])
        search_context = "\n\n".join([part for part in [social_context, tavily_context] if part])
        _log_search_event(
            "search.context.built",
            tavily_context_chars=len(tavily_context),
            social_context_chars=len(social_context),
            tavily_context_preview=tavily_context[:2000],
            social_context_preview=social_context[:3000],
        )

    if not search_context:
        raise RuntimeError("【Tavily 错误】联网检索成功，但未获得有效网页参考内容。")

    if tavily_results and os.getenv("FOOD_SEARCH_TAVILY_FAST_RETURN", "1") == "1" and len(social_fast_recipes) < rec_count:
        try:
            needed_count = max(1, rec_count - len(social_fast_recipes))
            fast_recipes = await _recipes_from_tavily_results(cleaned_query, tavily_results, needed_count, exclude_names)
            merged_recipes = list(social_fast_recipes)
            existing_names = {str(item.get("name") or "") for item in merged_recipes}
            for recipe in fast_recipes:
                if str(recipe.get("name") or "") not in existing_names:
                    merged_recipes.append(recipe)
                    existing_names.add(str(recipe.get("name") or ""))
                if len(merged_recipes) >= rec_count:
                    break
            _log_search_event(
                "search.final",
                elapsed_ms=_elapsed_ms(overall_started),
                result_source="social_plus_tavily_summary_fast" if social_fast_recipes else "tavily_summary_fast",
                result_count=len(merged_recipes),
                recipes=_recipe_log_summary(merged_recipes),
            )
            return _attach_debug_timeline(merged_recipes)
        except Exception as exc:
            _log_search_event(
                "tavily.summary.fast_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )

    exclude_str = ", ".join(exclude_names) if exclude_names else "无"
    # 调用大模型生成结构化菜谱
    prompt = f"""你是一个精通中华美食的资深特级主厨。请根据以下关于 '{cleaned_query}' 的联网多源搜索内容，整合提炼生成最多 {rec_count} 个不同的最正宗家常菜谱。
请务必确保生成的菜谱不包含以下已排除的菜品名称：[{exclude_str}]。

联网搜索参考上下文：
{search_context}

生成的菜谱必须是一个严格 JSON 数组（不要包含在 markdown ```json 语法块中，直接返回 JSON 字符串本身），包含最多 {rec_count} 个不同的食谱对象。格式如下：
[
  {{
    "name": "经典{query}（或者基于搜索得出的具体菜名，如葱爆牛肉、小炒牛肉等）",
    "category": "菜系分类",
    "source": "Tavily多源整合(下厨房/小红书等)",
    "image_url": "系统内置图片路径，请根据菜品类型仅从以下4个中选择最贴切的1个：
                  /images/classic_tomato_egg.png (适合番茄炒蛋、番茄汤等红黄色系菜肴)
                  /images/steamed_duck.png (适合鸡鸭鱼肉等硬菜)
                  /images/old_chuan_lu_hotpot.png (适合火锅、麻辣香锅、川菜等红油辣菜)
                  /images/cantonese_dim_sum.png (适合点心、凉菜、小吃)
                  如果没有最贴切的，请默认使用 /images/steamed_duck.png",
    "description": "菜品的一句话风味描述，字数在30-50字之间",
    "ingredients": [
        "食材名称 精确用量克数/个（如：五花肉 300g）"
    ],
    "condiments": [
        "调料名称 精确克数/勺（如：生抽 10ml）"
    ],
    "steps": [
        "【去腥/新鲜度处理】步骤描述，包含如何根据感官（如气味、色泽）判断食材新鲜度或焯水去腥细节",
        "【物态判断-火候控制】步骤描述，必须包含具体的物理视觉/听觉/状态描述（如：煸炒至肉片翻卷呈“灯盏窝”状）",
        "【调味/物理平替】步骤描述，必须包含如何精细调味，以及如果缺失某种调料时的物理平替方案（如生抽没用可以用盐糖加水代替）"
    ],
    "calories": 估算的整型卡路里数（如：350）
  }}
]
"""

    safe_print("\n" + "="*80, flush=True)
    safe_print(f"【LLM 真实调用】请求大模型进行多源食谱提炼与规范化...", flush=True)
    safe_print(f"  模型: {llm_model}", flush=True)
    safe_print(f"  API Base: {llm_base}", flush=True)
    safe_print("="*80 + "\n", flush=True)

    recipe_data = None
    for attempt in range(retries):
        llm_started = time.perf_counter()
        _log_search_event(
            "llm.attempt.start",
            attempt=attempt + 1,
            model=llm_model,
            base_url=llm_base,
            prompt_chars=len(prompt),
        )
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{llm_base}/chat/completions",
                    headers={"Authorization": f"Bearer {llm_key}"},
                    json={
                        "model": llm_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.2
                    },
                    timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "35"))
                )
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    recipe_data = _parse_recipe_json(content)
                    if isinstance(recipe_data, dict):
                        recipe_data = [recipe_data]
                    _log_search_event(
                        "llm.attempt.success",
                        attempt=attempt + 1,
                        elapsed_ms=_elapsed_ms(llm_started),
                        response_chars=len(content),
                        raw_content_preview=content[:2000],
                        recipe_count=len(recipe_data or []),
                        parsed_recipes=_recipe_log_summary(recipe_data or []),
                    )
                    break
                else:
                    raise RuntimeError(f"接口返回错误状态码: {resp.status_code}，响应: {resp.text}")
        except Exception as e:
            _log_search_event(
                "llm.attempt.error",
                attempt=attempt + 1,
                elapsed_ms=_elapsed_ms(llm_started),
                error_type=type(e).__name__,
                error=str(e)[:500],
            )
            wait_time = 1.5 * (attempt + 1)
            safe_print(f"【LLM 提炼尝试 {attempt+1}/{retries} 失败】({e})。正在重试...", flush=True)
            if attempt < retries - 1:
                await asyncio.sleep(wait_time)
            else:
                if mode == "online_first" and os.getenv("FOOD_SEARCH_ALLOW_FALLBACK", "0") == "1":
                    safe_print("【LLM 降级】结构化提炼失败，显式允许 fallback，改用本地缓存。", flush=True)
                    return _attach_debug_timeline(_search_recipes_from_local_cache(query, rec_count, exclude_names))
                safe_print("【LLM 快速提炼】大模型未在时限内返回，改用 Tavily 搜索摘要生成结构化候选。", flush=True)
                fallback_recipes = await _recipes_from_social_results(cleaned_query, social_results, rec_count)
                fallback_source = "social_details" if fallback_recipes else "tavily_summary"
                if not fallback_recipes:
                    fallback_recipes = await _recipes_from_tavily_results(cleaned_query, tavily_results, rec_count, exclude_names)
                _log_search_event(
                    "tavily.summary.extraction",
                    result_source=fallback_source,
                    result_count=len(fallback_recipes),
                    recipes=_recipe_log_summary(fallback_recipes),
                )
                _log_search_event(
                    "search.final",
                    elapsed_ms=_elapsed_ms(overall_started),
                    result_source=fallback_source,
                    result_count=len(fallback_recipes),
                    recipes=_recipe_log_summary(fallback_recipes),
                )
                return _attach_debug_timeline(fallback_recipes)

    if recipe_data:
        safe_print("\n" + "="*80, flush=True)
        safe_print(f"【LLM 提炼成功】成功整合成结构化食谱 JSON 列表。共 {len(recipe_data)} 个菜谱项目。", flush=True)
        safe_print("="*80 + "\n", flush=True)
        _log_search_event(
            "search.final",
            elapsed_ms=_elapsed_ms(overall_started),
            result_source="llm",
            result_count=len(recipe_data),
            recipes=_recipe_log_summary(recipe_data),
        )
        return _attach_debug_timeline(recipe_data)

    raise RuntimeError("【LLM 错误】大模型提炼失败。")


async def search_xhs_recipes_only(query: str, rec_count: int = 1, exclude_names: list[str] = None) -> list[dict]:
    """Search and extract recipe details from XHS only. This never falls through to Tavily."""
    SEARCH_TIMELINE.set([])
    overall_started = time.perf_counter()
    cleaned_query = clean_query(query)
    exclude_names = exclude_names or []
    _log_search_event(
        "search.xhs_only.start",
        query=query,
        cleaned_query=cleaned_query,
        rec_count=rec_count,
    )
    if os.getenv("AIONE_ENABLED", "0") != "1":
        _log_search_event("social.disabled", reason="AIONE_ENABLED is not 1")
        return []

    social_started = time.perf_counter()
    social_results_from_cache = False
    social_results = _get_cached_xhs_social_results(cleaned_query)
    if social_results is None:
        social_results = await _search_social_sources(cleaned_query, allow_tavily_fallback=False)
        _set_cached_xhs_social_results(cleaned_query, social_results)
    else:
        social_results_from_cache = True
    _log_search_event(
        "social.complete",
        elapsed_ms=_elapsed_ms(social_started),
        result_count=len(social_results),
        platforms=[r.get("platform") for r in social_results],
    )

    recipes = await _recipes_from_social_results(cleaned_query, social_results, rec_count)
    if (
        not recipes
        and social_results_from_cache
        and os.getenv("AIONE_XHS_REFRESH_WEAK_CACHE", "1") == "1"
    ):
        _log_search_event(
            "social.xhs.search_cache.weak_refresh",
            reason="cached_results_did_not_extract_recipe",
            result_count=len(social_results),
        )
        social_started = time.perf_counter()
        social_results = await _search_social_sources(cleaned_query, allow_tavily_fallback=False)
        _set_cached_xhs_social_results(cleaned_query, social_results)
        _log_search_event(
            "social.complete",
            elapsed_ms=_elapsed_ms(social_started),
            result_count=len(social_results),
            platforms=[r.get("platform") for r in social_results],
            cache_hit=False,
        )
        recipes = await _recipes_from_social_results(cleaned_query, social_results, rec_count)

    filtered = []
    for recipe in recipes:
        name = str(recipe.get("name") or "")
        if any(excluded and (excluded in name or name in excluded) for excluded in exclude_names):
            continue
        filtered.append(recipe)
        if len(filtered) >= rec_count:
            break
    _log_search_event(
        "search.xhs_only.final",
        elapsed_ms=_elapsed_ms(overall_started),
        result_count=len(filtered),
        recipes=_recipe_log_summary(filtered),
    )
    return _attach_debug_timeline(filtered)


async def search_recipe_images_online(query: str, limit: int = 4) -> dict:
    """Fetch recipe images only. Tavily is used as an image source, not as a recipe-structure source."""
    SEARCH_TIMELINE.set([])
    started = time.perf_counter()
    tavily_key = os.getenv("TAVILY_API_KEY")
    cleaned_query = clean_recipe_image_query(query)
    if not tavily_key or tavily_key == "your_tavily_api_key_here":
        _log_search_event("tavily.image_only.skipped", query=query, reason="missing_tavily_key")
        return {
            "image_urls": [],
            "source_url": "",
            "source": "Tavily 图片检索未启用",
            "debug_timeline": SEARCH_TIMELINE.get(),
        }

    search_query = f"{cleaned_query} 下厨房 菜谱 做法 成品图 美食 图片"
    _log_search_event("tavily.image_only.start", query=query, search_query=search_query, limit=limit)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": search_query,
                    "max_results": max(5, limit),
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": True,
                    "include_image_descriptions": os.getenv("TAVILY_INCLUDE_IMAGE_DESCRIPTIONS", "1") == "1",
                },
                timeout=float(os.getenv("TAVILY_IMAGE_TIMEOUT_SECONDS", os.getenv("TAVILY_TIMEOUT_SECONDS", "8"))),
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        _log_search_event(
            "tavily.image_only.error",
            elapsed_ms=_elapsed_ms(started),
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        return {
            "image_urls": [],
            "source_url": "",
            "source": "Tavily 图片检索失败",
            "debug_timeline": SEARCH_TIMELINE.get(),
        }

    results = data.get("results") or []
    top_images = data.get("images") or []
    if top_images:
        for item in results:
            if isinstance(item, dict) and not item.get("images"):
                item["images"] = top_images

    images: list[str] = []
    source_url = ""
    for result in results:
        for image_url in _tavily_images_for_recipe(cleaned_query, result, limit=limit):
            if image_url not in images:
                images.append(image_url)
        if images and not source_url:
            source_url = result.get("url") or ""
        if len(images) >= limit:
            break

    _log_search_event(
        "tavily.image_only.complete",
        elapsed_ms=_elapsed_ms(started),
        result_count=len(results),
        top_image_count=len(top_images) if isinstance(top_images, list) else 0,
        selected_image_count=len(images),
        selected_images=images[:limit],
        first_source_url=source_url,
    )
    return {
        "image_urls": images[:limit],
        "source_url": source_url,
        "source": "Tavily 图片检索",
        "debug_timeline": SEARCH_TIMELINE.get(),
    }

def _parse_recipe_json(content: str):
    """Parse model output that should be JSON but may include markdown fences or extra text."""
    content = content.strip()
    if content.startswith("```"):
        parts = content.split("```")
        content = parts[1] if len(parts) > 1 else content
        if content.startswith("json"):
            content = content[4:].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start:end + 1])
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start:end + 1])
        raise

def _search_recipes_from_local_cache(query: str, rec_count: int = 3, exclude_names: list[str] = None) -> list[dict]:
    """Explicit offline-only cache for tests and demos; never used unless mode allows it."""
    cleaned_query = clean_query(query)
    exclude_names = exclude_names or []
    ranked = []
    for key, recipe in MOCK_RECIPES.items():
        name = recipe["name"]
        if any(excluded in name or name in excluded for excluded in exclude_names):
            continue
        score = 0
        if cleaned_query and (cleaned_query in key or key in cleaned_query or cleaned_query in name):
            score += 10
        if "粤菜" in query and ("鸡" in name or recipe.get("category") == "粤菜"):
            score += 5
        ranked.append((score, recipe))

    ranked.sort(key=lambda item: item[0], reverse=True)
    if cleaned_query and (not ranked or ranked[0][0] <= 0):
        return [_generic_local_recipe_for_query(query)]
    selected = [recipe.copy() for score, recipe in ranked[:rec_count]]
    return selected

def _score_tavily_recipe_result(query: str, result: dict) -> int:
    title = result.get("title") or ""
    content = result.get("content") or ""
    host = urllib.parse.urlparse(result.get("url") or "").netloc.lower()
    score = 0
    if query and query in title:
        score += 80
    if query and query in content:
        score += 25
    preferred_hosts = [
        "xiachufang.com",
        "douguo.com",
        "meishij.net",
        "food.ltn.com.tw",
        "hk01.com",
        "startsmart.gov.hk",
    ]
    if any(host.endswith(item) or item in host for item in preferred_hosts):
        score += 35
    noisy_hosts = ["facebook.com", "youtube.com", "youtu.be", "tiktok.com"]
    if any(item in host for item in noisy_hosts):
        score -= 30
    return score

def _has_concrete_recipe_fields(recipe: dict) -> bool:
    ingredients = [str(item) for item in recipe.get("ingredients") or []]
    condiments = [str(item) for item in recipe.get("condiments") or []]
    steps = [str(item) for item in recipe.get("steps") or []]
    joined = "\n".join(ingredients + condiments + steps)
    weak_words = [
        "请参考", "原文用量", "原文调味", "小红书来源", "信息不足",
        "主食材 适量", "辅料 适量", "参考正文", "按需", "若干",
    ]
    if any(word in joined for word in weak_words):
        return False
    return len(ingredients) >= 3 and len(condiments) >= 3 and len(steps) >= 4


def _recipe_local_image_for_name(name: str) -> str:
    text = name or ""
    if any(word in text for word in ["番茄", "西红柿", "蛋花"]):
        return "/images/classic_tomato_egg.png"
    if any(word in text for word in ["火锅", "麻辣", "辣子", "水煮", "川", "香辣"]):
        return "/images/old_chuan_lu_hotpot.png"
    if any(word in text for word in ["点心", "小吃", "凉菜", "鸡爪"]):
        return "/images/cantonese_dim_sum.png"
    return "/images/steamed_duck.png"


def _generic_local_recipe_for_query(query: str) -> dict:
    name = clean_query(query) or "家常小炒"
    if any(word in name for word in ["宫保", "鸡丁", "鸡肉", "鸡"]):
        ingredients = ["鸡腿肉或鸡胸肉 250g", "熟花生米 40g", "黄瓜 半根"]
        condiments = ["生抽 15ml", "香醋 10ml", "白糖 8g", "干辣椒 4个", "淀粉 6g"]
        steps = [
            "鸡肉切丁，用少量生抽和淀粉抓匀腌 10 分钟，黄瓜切丁备用。",
            "热锅冷油，下鸡丁中火滑炒至表面变白、边缘微微收紧后盛出。",
            "锅中留底油，下干辣椒小火炒香，倒入鸡丁和黄瓜丁大火翻炒。",
            "加入生抽、香醋、白糖调成的料汁，炒到汤汁包裹鸡丁后撒入花生米出锅。",
        ]
        desc = "酸甜微辣，鸡丁嫩滑，适合作为下饭主菜。"
    elif any(word in name for word in ["牛肉", "黄牛肉"]):
        ingredients = ["牛里脊 250g", "青椒 2个", "姜蒜 适量"]
        condiments = ["生抽 15ml", "蚝油 10ml", "料酒 10ml", "淀粉 6g", "食用油 20ml"]
        steps = [
            "牛肉逆纹切薄片，用生抽、料酒、淀粉和少量油抓匀腌 10 分钟。",
            "热锅宽油，油温升高后下牛肉快速滑散，变色立刻盛出。",
            "锅中留底油爆香姜蒜，下青椒大火炒到边缘微皱。",
            "倒回牛肉，加蚝油快速翻匀，牛肉刚熟且仍有弹性时出锅。",
        ]
        desc = "牛肉嫩滑，青椒清香，适合多人餐里的荤菜槽位。"
    elif any(word in name for word in ["鱼", "鲈鱼", "鳕鱼"]):
        ingredients = ["鲜鱼 1条约500g", "姜片 6片", "葱 2根"]
        condiments = ["蒸鱼豉油 20ml", "料酒 10ml", "食用油 15ml", "盐 2g"]
        steps = [
            "鱼处理干净后擦干水分，鱼身划刀，抹少量盐和料酒去腥。",
            "盘底铺姜片和葱段，鱼身再放姜片，水开上汽后入锅。",
            "中大火蒸 8-10 分钟，鱼眼发白凸起、鱼肉能轻松剥离即熟。",
            "倒掉盘中腥水，淋蒸鱼豉油，铺葱丝后浇热油激香。",
        ]
        desc = "清淡鲜嫩，适合想吃不油腻的单人或家庭餐。"
    elif any(word in name for word in ["汤", "蛋花", "紫菜"]):
        ingredients = ["鸡蛋 1个", "紫菜 8g", "嫩豆腐 半块"]
        condiments = ["盐 3g", "白胡椒粉 1g", "香油 3ml", "葱花 适量"]
        steps = [
            "鸡蛋打散，豆腐切小块，紫菜撕成小片备用。",
            "锅中加水烧开，下豆腐煮 2 分钟，转小火保持微沸。",
            "沿锅边淋入蛋液，看到蛋花浮起后加入紫菜。",
            "加盐、白胡椒和香油调味，撒葱花后关火。",
        ]
        desc = "清爽快手，适合作为多人餐的汤品补位。"
    elif any(word in name for word in ["菜", "素", "西兰花", "生菜", "菜心"]):
        ingredients = ["时令绿叶菜 300g", "大蒜 4瓣", "清水 适量"]
        condiments = ["蚝油 12ml", "盐 2g", "食用油 15ml", "淀粉 4g"]
        steps = [
            "蔬菜洗净沥干，大蒜切末；菜梗较粗时先切开方便成熟。",
            "水开后加少量盐和油，蔬菜焯 20-40 秒至颜色变翠绿后捞出。",
            "锅中热油爆香蒜末，加入蚝油和少量清水煮开。",
            "淋入水淀粉收成薄芡，浇在蔬菜上或回锅快速翻匀即可。",
        ]
        desc = "清爽蒜香，适合补充蔬菜和素菜槽位。"
    else:
        ingredients = [f"{name}主料 250g", "葱姜蒜 适量", "配菜 150g"]
        condiments = ["生抽 15ml", "盐 3g", "食用油 20ml", "料酒 10ml"]
        steps = [
            "主料清洗后切成适口大小，配菜切好备用，闻到明显异味时不要使用。",
            "热锅冷油，先下葱姜蒜炒香，再下主料中火翻炒至表面变色。",
            "加入生抽、料酒和盐调味，必要时加少量清水焖 3-5 分钟。",
            "观察主料完全成熟、汤汁略收浓后，下配菜翻匀出锅。",
        ]
        desc = f"{name}的家常兜底做法，适合在联网抽取失败时参考。"
    return {
        "name": name,
        "category": "本地精选兜底",
        "source": "本地精选模板（Tavily 抽取不足时兜底）",
        "source_url": "",
        "image_url": _recipe_local_image_for_name(name),
        "source_image_urls": [],
        "description": desc,
        "ingredients": ingredients,
        "condiments": condiments,
        "steps": steps,
        "calories": 300,
    }


def _is_bad_tavily_image_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return True
    lowered = url.lower()
    bad_markers = [
        "favicon",
        "emoji",
        "avatar",
        "profile",
        "logo",
        "sprite",
        ".svg",
        "_100w_100h",
        "/w/100/h/100",
        "topapp",
        "google_widget/crawler",
        "lookaside.fbsbx.com",
        "static.xx.fbcdn.net",
        "/dist/waffle/img/",
        "iplocation",
        "qrcode",
        "meishichina.com",
        "meishitx.com",
    ]
    return any(marker in lowered for marker in bad_markers)


def _tavily_image_score(name: str, url: str, meta_text: str, result_text: str, base_score: int = 0) -> float:
    if _is_bad_tavily_image_url(url):
        return -100
    name = (name or "").strip()
    text = f"{meta_text} {result_text[:220]}".strip()
    lowered_text = text.lower()
    if any(word in lowered_text for word in ["profile picture", "logo", "avatar", "icon"]):
        return -100

    score = float(base_score or 0)
    if name and name in text:
        score += 5
    if any(word in text for word in ["做法", "步骤", "食材", "菜谱", "家常", "美食", "下饭"]):
        score += 2
    if _is_relevant_social_title(text, name):
        score += 2

    host = urllib.parse.urlparse(url).netloc.lower()
    if any(domain in host for domain in ["chuimg.com", "xiachufang.com", "lkk.com", "wecook.cn", "itc.cn", "ytimg.com"]):
        score += 2
    if any(domain in host for domain in ["instagram.com", "facebook.com"]):
        score -= 2
    return score


def _tavily_images_for_recipe(name: str, result: dict, limit: int = 4) -> list[str]:
    title = result.get("title") or ""
    content = result.get("content") or ""
    text = f"{title} {content[:300]}"
    if name and not _is_relevant_social_title(text, name):
        return []

    candidates: list[tuple[float, str]] = []
    seen: set[str] = set()

    for key in ["image_url", "thumbnail", "raw_content_image", "image"]:
        value = result.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in seen:
            seen.add(value)
            score = _tavily_image_score(name, value, title, text, 1)
            if score > 0:
                candidates.append((score, value))

    images = result.get("images")
    if isinstance(images, list):
        for value in images:
            url = ""
            meta_text = ""
            base_score = 0
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                url = value
            if isinstance(value, dict):
                url = value.get("url") or value.get("src") or value.get("image_url")
                meta_text = " ".join(
                    str(value.get(key) or "")
                    for key in ["title", "description", "alt", "caption"]
                )
                base_score = _safe_int(value.get("score"), 0)
            if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            score = _tavily_image_score(name, url, meta_text, text, base_score)
            if score > 0:
                candidates.append((score, url))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in candidates[:limit]]


def _tavily_image_for_recipe(name: str, result: dict) -> str:
    images = _tavily_images_for_recipe(name, result, limit=1)
    return images[0] if images else ""

def _looks_like_recipe_detail_text(text: str) -> bool:
    if not text or len(text.strip()) < 40:
        return False
    recipe_words = [
        "食材", "调料", "做法", "步骤", "用料", "配料", "教程",
        "克", "g", "ml", "勺", "分钟", "腌", "焯", "炒", "煮", "蒸",
        "下锅", "热锅", "小火", "大火", "生抽", "老抽", "蚝油", "盐",
    ]
    score = sum(1 for word in recipe_words if word in text)
    numbered_steps = len(re.findall(r"(^|\n)\s*(\d+[\.\、]|[一二三四五六七八九十]+[、.])", text))
    return score >= 3 or numbered_steps >= 2

async def _extract_recipe_from_xhs_detail_with_llm(query: str, detail: dict) -> dict | None:
    llm_key = os.getenv("LLM_API_KEY")
    llm_base = os.getenv("LLM_API_BASE", "https://api.bianxie.ai/v1")
    llm_model = os.getenv("LLM_MODEL", "deepseek-v4-pro")
    if not llm_key or llm_key == "your_llm_api_key_here":
        return None

    title = detail.get("title") or query
    desc = (detail.get("desc") or "").replace("\r", "\n").strip()
    if len(desc) < 20:
        return None
    if os.getenv("AIONE_XHS_RECIPE_SKIP_WEAK_TEXT", "1") == "1" and not _looks_like_recipe_detail_text(f"{title}\n{desc}"):
        _log_search_event(
            "social.xhs.recipe_extract.skipped",
            title=title,
            reason="weak_recipe_text",
        )
        return None
    cached = _get_cached_xhs_recipe_extract(query, detail)
    if cached:
        return cached

    prompt = f"""
你是一个家常菜谱结构化提取器。请只根据下面的小红书笔记标题和正文，提炼出可以直接照做的菜谱。

硬性要求：
1. 必须输出具体食材、具体调料、具体做法步骤。
2. 不允许输出“请参考原文”“原文用量”“看小红书”等占位句。
3. 如果正文缺少明确用量，可以基于家常一人份/一盘菜做合理估算，但要写出具体数量。
4. 不要输出思考过程，只返回 JSON 对象。

目标菜名：{query}
笔记标题：{title}
笔记正文：
{desc[:1600]}

返回格式：
{{
  "name": "具体菜名",
  "description": "30字以内风味说明",
  "ingredients": ["食材 用量", "食材 用量"],
  "condiments": ["调料 用量", "调料 用量"],
  "steps": ["步骤1", "步骤2", "步骤3"],
  "calories": 300
}}
""".strip()
    body = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON 对象，不输出 Markdown，不输出思考过程。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": int(os.getenv("AIONE_XHS_RECIPE_EXTRACT_MAX_TOKENS", "750") or "750"),
    }
    if os.getenv("LLM_SEND_ENABLE_THINKING", "0") == "1":
        body["enable_thinking"] = False
    started = time.perf_counter()
    timeout_seconds = float(
        os.getenv(
            "AIONE_XHS_RECIPE_EXTRACT_TIMEOUT_SECONDS",
            os.getenv("LLM_FAST_TIMEOUT_SECONDS", "6"),
        )
    )
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{llm_base}/chat/completions",
                headers={"Authorization": f"Bearer {llm_key}"},
                json=body,
                timeout=timeout_seconds,
            )
            if resp.status_code >= 400 and "enable_thinking" in body:
                body.pop("enable_thinking", None)
                resp = await client.post(
                    f"{llm_base}/chat/completions",
                    headers={"Authorization": f"Bearer {llm_key}"},
                    json=body,
                    timeout=timeout_seconds,
                )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        parsed = _parse_recipe_json(content)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            return None
        parsed.setdefault("name", query)
        parsed.setdefault("description", _clean_social_text(desc, fallback=title, limit=80))
        parsed.setdefault("calories", 300)
        if not _has_concrete_recipe_fields(parsed):
            _log_search_event(
                "social.xhs.recipe_extract.rejected",
                elapsed_ms=_elapsed_ms(started),
                title=title,
                reason="missing_concrete_fields",
                raw_content_preview=content[:500],
            )
            return None
        _log_search_event(
            "social.xhs.recipe_extract.success",
            elapsed_ms=_elapsed_ms(started),
            title=title,
            recipe=_recipe_log_summary([parsed])[0],
        )
        _set_cached_xhs_recipe_extract(query, detail, parsed)
        return parsed
    except Exception as exc:
        _log_search_event(
            "social.xhs.recipe_extract.error",
            elapsed_ms=_elapsed_ms(started),
            title=title,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        return None

async def _recipes_from_social_results(query: str, social_results: list[dict], rec_count: int = 3) -> list[dict]:
    recipes = []
    for result in social_results:
        if result.get("platform") != "xhs":
            continue
        for detail in result.get("details") or []:
            title = detail.get("title") or query
            desc = (detail.get("desc") or "").strip()
            extracted = await _extract_recipe_from_xhs_detail_with_llm(query, detail)
            if not extracted:
                continue
            clean_desc = _clean_social_text(desc, fallback=title, limit=120)
            image_urls = detail.get("image_urls") or []
            recipes.append({
                "name": extracted.get("name") or (query if query and _is_relevant_social_title(title, query) else title),
                "category": "小红书高互动菜谱",
                "source": "小红书详情提取",
                "source_url": detail.get("safe_url"),
                "image_url": image_urls[0] if image_urls else "/images/classic_tomato_egg.png",
                "source_image_urls": image_urls,
                "description": extracted.get("description") or clean_desc,
                "ingredients": extracted.get("ingredients") or [],
                "condiments": extracted.get("condiments") or [],
                "steps": extracted.get("steps") or [],
                "calories": _safe_int(extracted.get("calories"), 300),
            })
            if len(recipes) >= rec_count:
                return recipes
    return recipes

async def _recipes_from_social_results(query: str, social_results: list[dict], rec_count: int = 3) -> list[dict]:
    details = []
    for result in social_results:
        if result.get("platform") == "xhs":
            details.extend(result.get("details") or [])

    if not details:
        return []

    extract_limit = max(1, int(os.getenv("AIONE_XHS_RECIPE_EXTRACT_LIMIT", "1") or "1"))
    concurrency = max(1, int(os.getenv("AIONE_XHS_RECIPE_EXTRACT_CONCURRENCY", "3") or "3"))
    selected_details = details[:extract_limit]
    semaphore = asyncio.Semaphore(concurrency)

    _log_search_event(
        "social.xhs.recipe_extract.batch_start",
        detail_count=len(details),
        extract_count=len(selected_details),
        concurrency=concurrency,
    )

    async def extract_one(detail: dict) -> tuple[dict, dict | None]:
        async with semaphore:
            return detail, await _extract_recipe_from_xhs_detail_with_llm(query, detail)

    extracted_items = await asyncio.gather(*(extract_one(detail) for detail in selected_details))
    recipes = []
    for detail, extracted in extracted_items:
        if not extracted:
            continue
        title = detail.get("title") or query
        desc = (detail.get("desc") or "").strip()
        clean_desc = _clean_social_text(desc, fallback=title, limit=120)
        image_urls = detail.get("image_urls") or []
        recipes.append({
            "name": extracted.get("name") or (query if query and _is_relevant_social_title(title, query) else title),
            "category": "小红书高互动菜谱",
            "source": "小红书详情提取",
            "source_url": detail.get("safe_url"),
            "image_url": image_urls[0] if image_urls else "/images/classic_tomato_egg.png",
            "source_image_urls": image_urls,
            "description": extracted.get("description") or clean_desc,
            "ingredients": extracted.get("ingredients") or [],
            "condiments": extracted.get("condiments") or [],
            "steps": extracted.get("steps") or [],
            "calories": _safe_int(extracted.get("calories"), 300),
        })
        if len(recipes) >= rec_count:
            return recipes
    return recipes

async def _extract_recipe_from_tavily_result_with_llm(query: str, result: dict) -> dict | None:
    llm_key = os.getenv("LLM_API_KEY")
    llm_base = os.getenv("LLM_API_BASE", "https://api.bianxie.ai/v1")
    llm_model = os.getenv("LLM_MODEL", "deepseek-v4-pro")
    if not llm_key or llm_key == "your_llm_api_key_here":
        return None

    cached = _get_cached_tavily_recipe_extract(query, result)
    if cached:
        return cached

    title = result.get("title") or query
    source_url = result.get("url") or ""
    content = (result.get("content") or result.get("raw_content") or "").strip()
    if len(content) < 40:
        return None

    prompt = f"""
你是一个网页菜谱结构化提取器。请只根据下面的网页标题、摘要和正文片段，提炼出可以直接照做的家常菜谱。

硬性要求：
1. 优先提取网页中明确写出的食材、调料和步骤。
2. 如果网页缺少个别用量，可以按家常一盘菜合理估算，但必须写出具体数量。
3. 不允许输出“主食材适量”“辅料适量”“请参考原文”等占位句。
4. 步骤至少包含食材处理、下锅火候、调味、成熟判断或出锅。
5. 不要输出思考过程，只返回 JSON 对象。

目标菜名：{query}
网页标题：{title}
网页链接：{source_url}
网页内容：
{content[:2200]}

返回格式：
{{
  "name": "具体菜名",
  "description": "30字以内风味说明",
  "ingredients": ["食材 用量", "食材 用量", "食材 用量"],
  "condiments": ["调料 用量", "调料 用量", "调料 用量"],
  "steps": ["步骤1", "步骤2", "步骤3", "步骤4"],
  "calories": 300
}}
""".strip()
    body = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON 对象，不输出 Markdown，不输出思考过程。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.15,
        "max_tokens": int(os.getenv("TAVILY_RECIPE_EXTRACT_MAX_TOKENS", "850") or "850"),
    }
    if os.getenv("LLM_SEND_ENABLE_THINKING", "0") == "1":
        body["enable_thinking"] = False

    started = time.perf_counter()
    timeout_seconds = float(os.getenv("TAVILY_RECIPE_EXTRACT_TIMEOUT_SECONDS", os.getenv("LLM_FAST_TIMEOUT_SECONDS", "8")))
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{llm_base}/chat/completions",
                headers={"Authorization": f"Bearer {llm_key}"},
                json=body,
                timeout=timeout_seconds,
            )
            if resp.status_code >= 400 and "enable_thinking" in body:
                body.pop("enable_thinking", None)
                resp = await client.post(
                    f"{llm_base}/chat/completions",
                    headers={"Authorization": f"Bearer {llm_key}"},
                    json=body,
                    timeout=timeout_seconds,
                )
        resp.raise_for_status()
        content_text = resp.json()["choices"][0]["message"]["content"].strip()
        parsed = _parse_recipe_json(content_text)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            return None
        parsed.setdefault("name", query)
        parsed.setdefault("description", (content[:80] or title)[:80])
        parsed.setdefault("calories", 300)
        if not _has_concrete_recipe_fields(parsed):
            _log_search_event(
                "tavily.recipe_extract.rejected",
                elapsed_ms=_elapsed_ms(started),
                title=title,
                reason="missing_concrete_fields",
                raw_content_preview=content_text[:500],
            )
            return None
        _log_search_event(
            "tavily.recipe_extract.success",
            elapsed_ms=_elapsed_ms(started),
            title=title,
            recipe=_recipe_log_summary([parsed])[0],
        )
        _set_cached_tavily_recipe_extract(query, result, parsed)
        return parsed
    except Exception as exc:
        _log_search_event(
            "tavily.recipe_extract.error",
            elapsed_ms=_elapsed_ms(started),
            title=title,
            error_type=type(exc).__name__,
            error=str(exc)[:500],
        )
        return None


async def _recipes_from_tavily_results(query: str, results: list[dict], rec_count: int = 3, exclude_names: list[str] = None) -> list[dict]:
    """Build structured recipe candidates from real Tavily results."""
    exclude_names = exclude_names or []
    useful_results = [
        r for r in results
        if r.get("title") and not any(excluded in r.get("title", "") for excluded in exclude_names)
    ]
    relevant_results = [
        r for r in useful_results
        if _is_relevant_social_title(f"{r.get('title') or ''} {(r.get('content') or '')[:300]}", query)
    ]
    relevant_ids = {id(item) for item in relevant_results}
    if relevant_results:
        useful_results = relevant_results + [item for item in useful_results if id(item) not in relevant_ids]
    useful_results = sorted(
        useful_results,
        key=lambda r: (id(r) in relevant_ids, _score_tavily_recipe_result(query, r)),
        reverse=True,
    )
    if not useful_results:
        raise RuntimeError("【Tavily 摘要提炼失败】没有可用搜索结果。")

    recipes = []
    for result in useful_results[: max(rec_count * 2, rec_count)]:
        title = re.sub(r"【步骤图】|的做法.*|_.*| - .*", "", result.get("title", "")).strip()
        content = (result.get("content") or "").strip()
        extracted = await _extract_recipe_from_tavily_result_with_llm(query, result)
        if not extracted:
            continue
        name = extracted.get("name") or (query if query and (query in title or query in content) else title or query)
        if query and not _is_relevant_social_title(f"{name} {title} {content[:300]}", query):
            _log_search_event(
                "tavily.recipe_extract.rejected",
                title=title,
                extracted_name=name,
                query=query,
                reason="name_not_relevant_to_candidate",
            )
            continue
        source_url = result.get("url", "")
        source_host = urllib.parse.urlparse(source_url).netloc or "Tavily"
        image_urls = _tavily_images_for_recipe(name, result, limit=4)
        image_url = image_urls[0] if image_urls else _recipe_local_image_for_name(name)
        recipes.append({
            "name": name,
            "category": "联网菜谱",
            "source": f"Tavily网页菜谱提取({source_host})",
            "source_url": source_url,
            "image_url": image_url,
            "source_image_urls": image_urls,
            "description": extracted.get("description") or content[:80] or f"来自 {source_host} 的联网菜谱摘要。",
            "ingredients": extracted.get("ingredients") or [],
            "condiments": extracted.get("condiments") or [],
            "steps": extracted.get("steps") or [],
            "calories": _safe_int(extracted.get("calories"), 300),
        })
        if len(recipes) >= rec_count:
            break
    if len(recipes) < rec_count:
        filler_excludes = exclude_names + [str(item.get("name") or "") for item in recipes]
        fallback_images: list[str] = []
        for result in useful_results:
            for image_url in _tavily_images_for_recipe(query, result, limit=4):
                if image_url not in fallback_images:
                    fallback_images.append(image_url)
            if len(fallback_images) >= 4:
                break
        for fallback in _search_recipes_from_local_cache(query, rec_count - len(recipes), filler_excludes):
            fallback = fallback.copy()
            if fallback_images:
                fallback["image_url"] = fallback_images[0]
                fallback["source_image_urls"] = fallback_images
                fallback["source"] = f"{fallback.get('source') or '本地模板'}（联网结果不足时补齐，图片来自 Tavily）"
            else:
                fallback["source"] = f"{fallback.get('source') or '本地模板'}（联网结果不足时补齐）"
            recipes.append(fallback)
            if len(recipes) >= rec_count:
                break
    return recipes

async def _search_social_sources(
    query: str,
    allow_tavily_fallback: bool = True,
    xhs_detail_limit: int | None = None,
    xhs_comment_store_lookup: bool = False,
) -> list[dict]:
    """
    Optional XHS/social search via all-in-one-aione.
    Douyin stays behind a separate flag because current results are video-heavy and unreliable for recipes.
    """
    if os.getenv("AIONE_ENABLED", "0") != "1":
        _log_search_event("social.disabled", reason="AIONE_ENABLED is not 1")
        return []

    cli_path = os.getenv("AIONE_CLI_PATH", "aione")
    if cli_path.endswith(".exe") and not Path(cli_path).exists():
        safe_print(f"【AIONE 跳过】未找到 CLI: {cli_path}", flush=True)
        _log_search_event("social.cli_missing", cli_path=cli_path)
        return []

    commands = [
        ("xhs", [cli_path, "xhs", "note", "search", "--query", query, "--page", "1", "--output", "json"]),
    ]
    if os.getenv("AIONE_DOUYIN_ENABLED", "0") == "1":
        commands.append(("douyin", [
            cli_path,
            "douyin",
            "work",
            "search-some-general",
            "--query",
            query,
            "--num",
            os.getenv("AIONE_DOUYIN_SEARCH_LIMIT", "8"),
            "--sort-type",
            os.getenv("AIONE_DOUYIN_SORT_TYPE", "0"),
            "--publish-time",
            os.getenv("AIONE_DOUYIN_PUBLISH_TIME", "0"),
            "--output",
            "json",
        ]))
    else:
        _log_search_event("social.douyin.disabled", reason="AIONE_DOUYIN_ENABLED is not 1")

    results = []
    for platform, command in commands:
        platform_started = time.perf_counter()
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                cwd=os.getcwd(),
                env=_aione_subprocess_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=float(os.getenv("AIONE_TIMEOUT_SECONDS", "20")),
            )
            _log_search_event(
                "social.platform.response",
                platform=platform,
                elapsed_ms=_elapsed_ms(platform_started),
                returncode=completed.returncode,
                stdout_chars=len(completed.stdout or ""),
                stderr_preview=(completed.stderr or "")[:300],
            )
            if completed.returncode != 0:
                safe_print(f"【AIONE {platform} 跳过】{completed.stderr.strip()[:240]}", flush=True)
                continue
            payload = completed.stdout.strip()
            if platform == "xhs":
                candidates = _extract_xhs_search_candidates(
                    payload,
                    query,
                    limit=int(os.getenv("AIONE_XHS_SEARCH_CANDIDATE_LIMIT", "6")),
                )
                _log_search_event(
                    "social.xhs.search_candidates",
                    elapsed_ms=_elapsed_ms(platform_started),
                    query=query,
                    candidate_count=len(candidates),
                    candidates=[
                        {
                            "note_id": item.get("note_id"),
                            "title": item.get("title"),
                            "author": item.get("author"),
                            "safe_url": _safe_xhs_note_url(item),
                            "liked_count": item.get("liked_count"),
                            "collected_count": item.get("collected_count"),
                            "comment_count": item.get("comment_count"),
                        }
                        for item in candidates
                    ],
                )
                details = await _fetch_xhs_note_details(cli_path, candidates, detail_limit=xhs_detail_limit)
                if details and xhs_comment_store_lookup:
                    lookup_limit = int(os.getenv("AIONE_XHS_COMMENT_STORE_LOOKUP_LIMIT", "3"))
                    lookup_count = 0
                    for detail in details:
                        current_name = _extract_store_name(detail.get("title", ""), query, detail.get("desc", ""))
                        cached_name = _get_cached_xhs_store_name(detail.get("note_id"))
                        if cached_name:
                            detail["comment_store_name"] = cached_name
                            continue
                        if current_name != "待从笔记确认店名":
                            continue
                        if lookup_count >= lookup_limit:
                            continue
                        comment_name = await _fetch_xhs_store_name_from_comments(cli_path, detail)
                        lookup_count += 1
                        if comment_name:
                            detail["comment_store_name"] = comment_name
                if details:
                    summary = _format_xhs_details(details)
                    _log_search_event(
                        "social.xhs.details.ready",
                        elapsed_ms=_elapsed_ms(platform_started),
                        detail_count=len(details),
                        summary_preview=summary[:900],
                    )
                    results.append({
                        "platform": "xhs",
                        "command": " ".join(command[1:]),
                        "summary": summary,
                        "details": details,
                        "image_urls": [
                            url
                            for detail in details
                            for url in (detail.get("image_urls") or [])
                        ],
                    })
                    continue
            summary = _summarize_aione_payload(platform, payload, query)
            if not summary:
                safe_print(f"【AIONE {platform} 跳过】未命中相关结果", flush=True)
                _log_search_event(
                    "social.platform.irrelevant",
                    platform=platform,
                    elapsed_ms=_elapsed_ms(platform_started),
                    query=query,
                )
                continue
            _log_search_event(
                "social.platform.relevant",
                platform=platform,
                elapsed_ms=_elapsed_ms(platform_started),
                summary_preview=summary[:500],
            )
            results.append({
                "platform": platform,
                "command": " ".join(command[1:]),
                "summary": summary,
            })
        except Exception as exc:
            _log_search_event(
                "social.platform.error",
                platform=platform,
                elapsed_ms=_elapsed_ms(platform_started),
                error_type=type(exc).__name__,
                error=str(exc)[:500],
            )
            safe_print(f"【AIONE {platform} 异常】{exc}", flush=True)
    if allow_tavily_fallback and not any(item.get("platform") == "xhs" for item in results):
        xhs_tavily = await _search_xhs_with_tavily(query)
        if xhs_tavily:
            results.append(xhs_tavily)
            _log_search_event(
                "social.xhs_tavily.relevant",
                summary_preview=xhs_tavily.get("summary", "")[:500],
            )
    return results


def _social_detail_to_place(detail: dict, fallback_query: str, kind: str) -> dict:
    title = detail.get("title") or fallback_query
    raw_desc = detail.get("desc") or ""
    desc = _clean_social_text(raw_desc, fallback=title, limit=220)
    images = detail.get("image_urls") or []
    source_text = f"{title} {raw_desc}"
    recommended_dishes = _extract_recommended_dishes(source_text)
    cached_store_name = _get_cached_xhs_store_name(detail.get("note_id"))
    store_name = (
        detail.get("comment_store_name")
        or cached_store_name
        or _extract_store_name(title, fallback_query, raw_desc)
    )
    address = (
        detail.get("address")
        or detail.get("poi_name")
        or detail.get("location")
        or detail.get("ip_location")
        or ""
    )
    return {
        "name": title[:28],
        "note_id": detail.get("note_id", ""),
        "store_name": store_name,
        "recommended_dishes": recommended_dishes,
        "kind": kind,
        "source": "小红书探店笔记",
        "source_url": detail.get("safe_url", ""),
        "image_url": images[0] if images else "/images/steamed_duck.png",
        "source_image_urls": images,
        "description": desc,
        "address": address,
        "liked_count": detail.get("liked_count", ""),
        "collected_count": detail.get("collected_count", ""),
        "comment_count": detail.get("comment_count", ""),
        "author": detail.get("author", ""),
        "raw_desc": raw_desc,
    }


def _normalize_place_name(name: str) -> str:
    return re.sub(r"[\s()（）【】\[\]·•\-—_、，,。.！!？?]+", "", str(name or "")).lower()


def _extract_store_name(title: str, fallback_query: str = "", body: str = "") -> str:
    combined = f"{title or ''}\n{body or ''}"
    combined = re.sub(r"#[^#\s]+(?:\[话题\])?#?", " ", combined)
    explicit_patterns = [
        r"(?:店名|店铺|餐厅|打卡店|位置|地址)[:：]\s*([^\n，,。；;]{2,28})",
        r"(?:在|去|冲|吃|打卡)\s*([^\n，,。；;]{2,24}(?:店|馆|餐厅|火锅|烤肉|酒楼|小馆|菜馆|食堂|料理|饭店|烧烤|茶餐厅))",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, combined)
        if match:
            candidate = _cleanup_store_name(match.group(1))
            if _looks_like_explicit_store_name(candidate):
                return candidate[:24]

    original = re.sub(r"[#【】\[\]「」]", " ", str(title or "")).strip()
    text = original
    text = re.sub(r"\s+", " ", text)
    shop_suffix_pattern = r"[\u4e00-\u9fa5A-Za-z0-9·]{2,24}(?:店|馆|餐厅|火锅|烤肉|酒楼|小馆|菜馆|食堂|料理|饭店|烧烤|茶餐厅)"
    suffix_matches = re.findall(shop_suffix_pattern, text)
    if suffix_matches:
        candidate = _cleanup_store_name(suffix_matches[0])
        if _looks_like_store_name(candidate):
            return candidate[:24]
    for sep in ["｜", "|", "：", ":", "，", ",", "。", "！", "!", "？", "?"]:
        if sep in text:
            parts = [part.strip() for part in text.split(sep) if part.strip()]
            if parts:
                text = max(parts, key=len)
                break
    text = re.sub(r"(探店|美食|推荐|必吃|真的|好吃|武汉|人均|攻略|附近|商圈)", "", text).strip()
    text = _cleanup_store_name(text)
    if not _looks_like_store_name(text):
        text = "待从笔记确认店名"
    return text[:24]


def _cleanup_store_name(name: str) -> str:
    name = re.sub(r"[\s#【】\[\]「」]+", "", str(name or ""))
    name = re.sub(r"(就在|就是|这家|一家|真的|好吃|必吃|推荐|附近|地址|位置)$", "", name)
    return name.strip("：:，,。；;！!?？")


def _looks_like_store_name(name: str) -> bool:
    if not name or len(name) < 2:
        return False
    if len(name) > 28:
        return False
    blocked = ["待从笔记", "推荐菜", "小红书", "工作日", "排队", "攻略", "商圈", "美食", "附近"]
    if any(word in name for word in blocked):
        return False
    return bool(re.search(r"(店|馆|餐厅|火锅|烤肉|酒楼|小馆|菜馆|食堂|料理|饭店|烧烤|茶餐厅)$", name))


def _looks_like_explicit_store_name(name: str) -> bool:
    if not name or len(name) < 2 or len(name) > 28:
        return False
    blocked = ["待从笔记", "推荐菜", "小红书", "工作日", "排队", "攻略", "商圈", "美食", "附近", "哪里", "什么"]
    if any(word in name for word in blocked):
        return False
    return bool(re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", name))


def _is_low_quality_social_store_name(name: str) -> bool:
    name = str(name or "").strip()
    if not name or name == "待从笔记确认店名":
        return True
    if len(name) < 3 or len(name) > 24:
        return True
    if re.match(r"^[的地得了和与及、，。！？\s]+", name):
        return True
    blocked = [
        "小红书", "探店", "攻略", "推荐", "附近", "商圈", "美食", "真的", "好吃",
        "工作日", "排队", "期散伙饭", "没有", "哪里", "什么", "这家", "一家",
    ]
    if any(word in name for word in blocked):
        return True
    if name.endswith(("店", "馆", "餐厅", "火锅", "烤肉", "酒楼", "小馆", "菜馆", "食堂", "料理", "饭店", "烧烤", "茶餐厅")):
        return False
    return not _looks_like_explicit_store_name(name)


def _extract_recommended_dishes(text: str) -> str:
    text = re.sub(r"#[^#\s]+(?:\[话题\])?#?", " ", str(text or ""))
    keywords = "鸡鸭鱼虾蟹牛羊肉蛙排骨锅串粉面饭粥汤豆腐茄子土豆藕菜椒辣烤炸煲"
    blocked = {
        "武汉", "探店", "美食", "推荐", "餐厅", "商圈", "地址", "预算", "人均", "好吃", "附近", "打卡",
        "工作日", "饭点", "排队", "原因", "一直", "真的", "今天", "这里", "每个", "已经", "都会",
        "小红书", "作者", "欢迎", "装修", "拍照", "店名",
    }
    candidates = []
    for item in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9·]{2,12}", text):
        if item in blocked or any(word in item for word in blocked):
            continue
        if len(item) > 8 and not any(item.endswith(suffix) for suffix in ["鸡", "鱼", "虾", "肉", "蛙", "锅", "饭", "面", "粉", "汤", "豆腐", "茄子"]):
            continue
        if any(ch in item for ch in keywords):
            candidates.append(item)
    deduped = []
    seen = set()
    for item in candidates:
        key = _normalize_place_name(item)
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
        if len(deduped) >= 3:
            break
    return "、".join(deduped) if deduped else "到店优先看招牌菜/高赞菜"


def _amap_rating(poi: dict) -> float | None:
    biz_ext = poi.get("biz_ext") or {}
    try:
        return float(biz_ext.get("rating"))
    except (TypeError, ValueError):
        return None


def _amap_location_tuple(poi: dict) -> tuple[float, float] | None:
    location = poi.get("location") or ""
    if "," not in location:
        return None
    try:
        lng_text, lat_text = location.split(",", 1)
        return float(lng_text), float(lat_text)
    except ValueError:
        return None


def _amap_photo_urls(poi: dict) -> list[str]:
    urls: list[str] = []
    for photo in poi.get("photos") or []:
        if isinstance(photo, dict):
            url = photo.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                urls.append(url)
    return urls


async def _amap_get_pois(client: httpx.AsyncClient, path: str, params: dict) -> list[dict]:
    key = os.getenv("GAODE_API_KEY", "")
    if not key or key == "your_gaode_api_key_here":
        return []
    clean_params = {k: v for k, v in params.items() if v not in (None, "")}
    clean_params["key"] = key
    try:
        resp = await client.get(
            f"https://restapi.amap.com/v3/{path}",
            params=clean_params,
            timeout=float(os.getenv("GAODE_POI_TIMEOUT_SECONDS", "5") or "5"),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            return []
        return data.get("pois") or []
    except (httpx.HTTPError, httpx.TimeoutException):
        return []


async def _amap_geocode_point(client: httpx.AsyncClient, address: str, city: str = "") -> tuple[float, float] | None:
    key = os.getenv("GAODE_API_KEY", "")
    if not address or not key or key == "your_gaode_api_key_here":
        return None
    try:
        resp = await client.get(
            "https://restapi.amap.com/v3/geocode/geo",
            params={"key": key, "address": address, "city": city or os.getenv("GAODE_DEFAULT_CITY", "北京")},
            timeout=float(os.getenv("GAODE_POI_TIMEOUT_SECONDS", "5") or "5"),
        )
        resp.raise_for_status()
        data = resp.json()
        geocodes = data.get("geocodes") or []
        if data.get("status") == "1" and geocodes:
            location = geocodes[0].get("location") or ""
            if "," in location:
                lng_text, lat_text = location.split(",", 1)
                return float(lng_text), float(lat_text)
    except (httpx.HTTPError, httpx.TimeoutException, ValueError):
        return None
    return None


async def search_gaode_restaurants(
    location: str,
    query: str = "",
    budget: float = 150,
    exclude_names: list[str] | None = None,
    center: tuple[float, float] | None = None,
    rec_count: int = 1,
    must_match_name: str | None = None,
) -> list[dict]:
    """
    Search Gaode restaurant POIs around a business area. Returns only rating >= 4.0
    and excludes names that overlap with social-source candidates.
    """
    city = os.getenv("GAODE_DEFAULT_CITY", "北京")
    exclude_keys = [_normalize_place_name(name) for name in (exclude_names or []) if name]
    keywords = [part for part in [location, query, "餐厅 美食"] if part]
    text_keyword = " ".join(keywords)
    raw: list[dict] = []
    search_center = center

    async with httpx.AsyncClient(follow_redirects=True) as client:
        text_pois = await _amap_get_pois(client, "place/text", {
            "keywords": text_keyword,
            "city": city,
            "children": 1,
            "offset": 20,
            "page": 1,
            "extensions": "all",
        })
        if search_center is None:
            search_center = await _amap_geocode_point(client, location, city)
        if search_center is None and text_pois:
            search_center = _amap_location_tuple(text_pois[0])
        if search_center:
            around_raw = []
            around_keywords = [
                f"{query} 餐厅" if query else "餐厅",
                "美食",
                "特色餐厅",
                "川菜" if "辣" in (query or "") or "川" in (query or "") else "",
            ]
            tasks = [
                _amap_get_pois(client, "place/around", {
                    "location": f"{search_center[0]},{search_center[1]}",
                    "keywords": keyword,
                    "radius": 3000,
                    "sortrule": "distance",
                    "offset": 20,
                    "page": 1,
                    "extensions": "all",
                })
                for keyword in around_keywords if keyword
            ]
            for pois in await asyncio.gather(*tasks):
                around_raw.extend(pois)
            raw.extend(around_raw or text_pois)
        else:
            raw.extend(text_pois)

    by_name: dict[str, dict] = {}
    must_match_key = _normalize_place_name(must_match_name or "")
    for poi in raw:
        name = poi.get("name") or ""
        name_key = _normalize_place_name(name)
        if not name_key:
            continue
        if must_match_key and not (must_match_key in name_key or name_key in must_match_key):
            continue
        if any(key and (key in name_key or name_key in key) for key in exclude_keys):
            continue
        rating = _amap_rating(poi)
        if rating is None or rating <= 4.0:
            continue
        if name_key not in by_name or rating > (by_name[name_key].get("_rating") or 0):
            by_name[name_key] = {**poi, "_rating": rating}

    ranked = sorted(
        by_name.values(),
        key=lambda item: (
            item.get("_rating") or 0,
            -int(item.get("distance") or 999999) if str(item.get("distance") or "").isdigit() else -999999,
        ),
        reverse=True,
    )
    results = []
    for poi in ranked[:rec_count]:
        biz_ext = poi.get("biz_ext") or {}
        rating = poi.get("_rating")
        avg_cost = biz_ext.get("cost") or budget
        address = poi.get("address") or poi.get("pname") or location
        distance = poi.get("distance")
        photo_urls = _amap_photo_urls(poi)
        desc_parts = [
            f"高德评分 {rating:.1f}/5",
            f"地址：{address}",
            f"距离商圈中心约 {distance} 米" if distance else "",
            f"人均参考 {avg_cost} 元" if avg_cost else "",
        ]
        results.append({
            "name": poi.get("name"),
            "store_name": poi.get("name"),
            "recommended_dishes": query or "到店优先看招牌菜/高赞菜",
            "kind": "restaurant",
            "source": "高德地图餐厅",
            "source_url": "",
            "image_url": photo_urls[0] if photo_urls else "/images/steamed_duck.png",
            "source_image_urls": photo_urls,
            "description": "；".join(part for part in desc_parts if part),
            "rating": rating,
            "avg_cost": avg_cost,
            "address": address,
            "distance": distance,
            "type": poi.get("type", ""),
            "raw_poi": {
                "id": poi.get("id"),
                "typecode": poi.get("typecode"),
                "location": poi.get("location"),
            },
        })
    return results


async def enrich_restaurants_with_gaode(
    restaurants: list[dict],
    location: str,
    budget: float = 150,
    center: tuple[float, float] | None = None,
) -> list[dict]:
    """Use Gaode to fill precise address/floor, rating and photos for XHS restaurants with clear store names."""
    enriched: list[dict] = []
    for rest in restaurants:
        store_name = rest.get("store_name") or ""
        if _is_low_quality_social_store_name(store_name):
            _log_search_event(
                "dining.social_candidate.rejected",
                store_name=store_name,
                title=rest.get("name"),
                reason="low_quality_store_name",
            )
            continue
        matches = await search_gaode_restaurants(
            location,
            store_name,
            budget,
            exclude_names=[],
            center=center,
            rec_count=1,
            must_match_name=store_name,
        )
        if not matches:
            _log_search_event(
                "dining.social_candidate.rejected",
                store_name=store_name,
                title=rest.get("name"),
                reason="gaode_no_reliable_match",
            )
            continue
        match = matches[0]
        rest = {
            **rest,
            "store_name": match.get("store_name") or store_name,
            "address": match.get("address") or rest.get("address") or location,
            "rating": match.get("rating"),
            "avg_cost": match.get("avg_cost") or rest.get("avg_cost"),
            "distance": match.get("distance") or rest.get("distance"),
            "gaode_type": match.get("type", ""),
            "gaode_raw_poi": match.get("raw_poi", {}),
            "source": "小红书探店笔记 + 高德校验",
        }
        if match.get("image_url") and match.get("image_url") != "/images/steamed_duck.png":
            rest["image_url"] = match["image_url"]
            rest["source_image_urls"] = match.get("source_image_urls") or rest.get("source_image_urls") or []
        enriched.append(rest)
    return enriched


async def enrich_restaurants_with_xhs_notes(
    restaurants: list[dict],
    location: str,
    query: str = "",
    rec_count: int = 3,
) -> list[dict]:
    """Attach XHS seed notes to Gaode restaurant candidates without using comments."""
    if not restaurants or os.getenv("AIONE_ENABLED", "0") != "1":
        enriched = []
        for rest in restaurants:
            item = rest.copy()
            item["xhs_note_status"] = "none"
            item["xhs_seed_text"] = "没有搜索到相关小红书种草内容"
            enriched.append(item)
        return enriched

    detail_limit = max(1, int(os.getenv("AIONE_XHS_RESTAURANT_NOTE_DETAIL_LIMIT", "1") or "1"))
    timeout_seconds = float(os.getenv("AIONE_XHS_RESTAURANT_NOTE_TIMEOUT_SECONDS", "6") or "6")

    async def enrich_one(rest: dict) -> dict:
        item = rest.copy()
        store_name = item.get("store_name") or item.get("name") or ""
        xhs_query = " ".join(part for part in [location, store_name, query, "推荐菜 招牌菜 探店"] if part)
        cached_results = _get_cached_xhs_social_results(xhs_query)
        live_lookup = os.getenv("AIONE_XHS_RESTAURANT_NOTE_LIVE_LOOKUP", "0") == "1"
        if cached_results is None and not live_lookup:
            item["xhs_note_status"] = "none"
            item["xhs_seed_text"] = "没有搜索到相关小红书种草内容"
            item["xhs_note_title"] = ""
            item["xhs_source_url"] = ""
            return item
        if cached_results is None:
            try:
                cached_results = await asyncio.wait_for(
                    _search_social_sources(
                        xhs_query,
                        allow_tavily_fallback=False,
                        xhs_detail_limit=detail_limit,
                        xhs_comment_store_lookup=False,
                    ),
                    timeout=timeout_seconds,
                )
                _set_cached_xhs_social_results(xhs_query, cached_results)
            except asyncio.TimeoutError:
                _log_search_event(
                    "dining.xhs_note.timeout",
                    elapsed_ms=int(timeout_seconds * 1000),
                    title=store_name,
                )
                cached_results = []
            except Exception as exc:
                _log_search_event(
                    "dining.xhs_note.error",
                    error_type=type(exc).__name__,
                    error=str(exc)[:400],
                    title=store_name,
                )
                cached_results = []

        detail = None
        for result in cached_results or []:
            if result.get("platform") != "xhs":
                continue
            for candidate in result.get("details") or []:
                text = f"{candidate.get('title') or ''} {candidate.get('desc') or ''}"
                if store_name and store_name not in text:
                    continue
                detail = candidate
                break
            if detail:
                break

        if not detail:
            item["xhs_note_status"] = "none"
            item["xhs_seed_text"] = "没有搜索到相关小红书种草内容"
            item["xhs_note_title"] = ""
            item["xhs_source_url"] = ""
            return item

        place = _social_detail_to_place(detail, xhs_query, "restaurant_note")
        images = place.get("source_image_urls") or []
        seed_text = place.get("description") or "没有搜索到相关小红书种草内容"
        dishes = place.get("recommended_dishes") or ""
        if dishes and dishes != "到店优先看招牌菜/高赞菜":
            item["recommended_dishes"] = dishes
        item["xhs_note_status"] = "found"
        item["xhs_note_title"] = place.get("name") or detail.get("title") or ""
        item["xhs_seed_text"] = seed_text
        item["xhs_source_url"] = place.get("source_url") or detail.get("safe_url") or ""
        item["xhs_author"] = place.get("author") or detail.get("author") or ""
        if images:
            item["image_url"] = images[0]
            item["source_image_urls"] = images
        item["source"] = f"{item.get('source') or '高德地图餐厅'} + 小红书种草"
        return item

    tasks = [enrich_one(rest) for rest in restaurants[:rec_count]]
    return await asyncio.gather(*tasks)


async def search_gaode_entertainment(
    location: str,
    query: str = "",
    center: tuple[float, float] | None = None,
    rec_count: int = 3,
) -> list[dict]:
    """Search nearby after-meal entertainment POIs around the selected business area."""
    city = os.getenv("GAODE_DEFAULT_CITY", "北京")
    search_center = center
    keywords = [
        item.strip()
        for item in os.getenv("GAODE_ENTERTAINMENT_KEYWORDS", "电影院,电玩城,台球,猫咖,展览").split(",")
        if item.strip()
    ]
    keyword_limit = max(1, int(os.getenv("GAODE_ENTERTAINMENT_KEYWORD_LIMIT", "5") or "5"))
    keywords = keywords[:keyword_limit]
    raw: list[dict] = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        if search_center is None:
            search_center = await _amap_geocode_point(client, location, city)
        if search_center:
            tasks = [
                _amap_get_pois(client, "place/around", {
                    "location": f"{search_center[0]},{search_center[1]}",
                    "keywords": keyword,
                    "radius": 3000,
                    "sortrule": "distance",
                    "offset": 12,
                    "page": 1,
                    "extensions": "all",
                })
                for keyword in keywords
            ]
            for pois in await asyncio.gather(*tasks):
                raw.extend(pois)
        else:
            tasks = [
                _amap_get_pois(client, "place/text", {
                    "keywords": f"{location} {keyword}",
                    "city": city,
                    "children": 1,
                    "offset": 12,
                    "page": 1,
                    "extensions": "all",
                })
                for keyword in keywords
            ]
            for pois in await asyncio.gather(*tasks):
                raw.extend(pois)

    blocked_type_words = ["餐饮", "美食", "住宿", "生活服务", "汽车", "医疗", "住宅", "公司", "学校"]
    by_name: dict[str, dict] = {}
    for poi in raw:
        name = poi.get("name") or ""
        type_text = poi.get("type") or ""
        name_key = _normalize_place_name(name)
        if not name_key:
            continue
        if any(word in type_text for word in blocked_type_words):
            continue
        rating = _amap_rating(poi)
        if rating is not None and rating < 4.0:
            continue
        distance = poi.get("distance")
        distance_value = int(distance) if str(distance or "").isdigit() else 999999
        score = (rating or 4.0) * 10 - min(distance_value / 300, 10)
        if name_key not in by_name or score > by_name[name_key].get("_score", -999):
            by_name[name_key] = {**poi, "_rating": rating, "_score": score}

    ranked = sorted(by_name.values(), key=lambda item: item.get("_score", 0), reverse=True)
    results = []
    for poi in ranked[:rec_count]:
        rating = poi.get("_rating")
        address = poi.get("address") or location
        distance = poi.get("distance")
        type_text = poi.get("type") or "休闲娱乐"
        desc_parts = [
            type_text.split(";")[-1] if type_text else "休闲娱乐",
            f"评分 {rating:.1f}" if rating else "",
            f"距离约 {distance} 米" if distance else "",
            f"地址：{address}" if address else "",
        ]
        results.append({
            "name": poi.get("name"),
            "kind": "after_meal",
            "source": "高德地图玩乐",
            "description": "；".join(part for part in desc_parts if part),
            "address": address,
            "distance": distance,
            "rating": rating,
            "type": type_text,
            "raw_poi": {
                "id": poi.get("id"),
                "typecode": poi.get("typecode"),
                "location": poi.get("location"),
            },
        })
    return results


async def search_xhs_dining_plan(location: str, query: str, budget: float = 150, rec_count: int = 3) -> dict:
    """
    Build dining-out candidates from XHS only. The result is restaurant-oriented,
    plus after-meal places to rest, walk around, drink, or play.
    """
    location = (location or "").strip()
    query = (query or "").strip()
    restaurant_query = " ".join(part for part in [location, query, "餐厅 探店 美食"] if part)
    after_query = " ".join(part for part in [location, "饭后 逛 玩 咖啡 甜品 攻略"] if part)

    configured_detail_limit = int(os.getenv("AIONE_XHS_DINING_DETAIL_LIMIT", "4"))
    detail_limit = max(1, min(configured_detail_limit, 6))
    comment_lookup_enabled = os.getenv("AIONE_XHS_COMMENT_STORE_LOOKUP", "0").lower() in {"1", "true", "yes", "on"}
    restaurant_results, after_results = await asyncio.gather(
        _search_social_sources(
            restaurant_query,
            allow_tavily_fallback=False,
            xhs_detail_limit=detail_limit,
            xhs_comment_store_lookup=comment_lookup_enabled,
        ),
        _search_social_sources(after_query, allow_tavily_fallback=False),
        return_exceptions=True,
    )

    restaurants = []
    if not isinstance(restaurant_results, Exception):
        for result in restaurant_results:
            if result.get("platform") != "xhs":
                continue
            for detail in result.get("details") or []:
                place = _social_detail_to_place(detail, restaurant_query, "restaurant")
                if not place.get("store_name") or place.get("store_name") == "待从笔记确认店名":
                    continue
                restaurants.append(place)
                if len(restaurants) >= rec_count:
                    break
            if len(restaurants) >= rec_count:
                break

    after_places = []
    if not isinstance(after_results, Exception):
        for result in after_results:
            if result.get("platform") != "xhs":
                continue
            for detail in result.get("details") or []:
                after_places.append(_social_detail_to_place(detail, after_query, "after_meal"))
                if len(after_places) >= 3:
                    break
            if len(after_places) >= 3:
                break

    return {
        "restaurants": _attach_debug_timeline(restaurants),
        "after_places": after_places,
        "restaurant_query": restaurant_query,
        "after_query": after_query,
    }

def search_nearby_restaurants(location: str, query: str, budget: float = 150, exclude_names: list[str] = None) -> list[dict]:
    """
    检索附近餐厅及玩乐项目，支持排除特定商户名以配合换一换和大范围挑选功能。
    """
    matched = []
    query_lower = query.lower()
    
    for rest in MOCK_RESTAURANTS:
        if exclude_names and rest["name"] in exclude_names:
            continue
            
        # 匹配口味且在预算内
        # 双向包含以及按 '/' 分割进行子标签匹配
        cuisine_lower = rest["cuisine"].lower()
        cuisine_tags = [t.strip() for t in cuisine_lower.split('/')]
        name_lower = rest["name"].lower()
        
        is_match = (
            query_lower in cuisine_lower or 
            any(tag in query_lower for tag in cuisine_tags) or 
            query_lower in name_lower or 
            name_lower in query_lower
        )
        
        if is_match and rest["avg_price"] <= budget:
            # 自动搭配周边的娱乐项目 and 甜点
            # 这是一个吃喝玩乐一条龙的模拟
            extended_info = rest.copy()
            extended_info["nearby_dessert"] = {
                "name": "喜茶(五道口店)",
                "distance": "120m",
                "recommended": "多肉葡萄, 芝芝绿妍"
            }
            extended_info["nearby_entertainment"] = {
                "name": "万达影城(五道口店)" if "五道口" in rest["location"] else "腾讯视频好时光微影院",
                "distance": "350m",
                "activities": ["看电影", "打台球"]
            }
            matched.append(extended_info)
            
    return matched
