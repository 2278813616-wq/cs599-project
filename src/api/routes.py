from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import json
import asyncio
import hashlib
from src.config.env import load_project_env
load_project_env()
import uuid
import time
import re
from typing import Optional, List, Any
import urllib.parse
import httpx
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

# 创建日志文件夹
os.makedirs("logs", exist_ok=True)

# 1. 过滤高频心跳/日志拉取，让控制台变得非常干净
class HeartbeatLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # 拦截包含心跳和审计日志高频拉取路由的访问记录
        if "/api/foodie/system/status" in msg or "/api/foodie/audit/logs" in msg:
            return False
        return True

# 2. 获取并配置 uvicorn.access
access_logger = logging.getLogger("uvicorn.access")
access_logger.addFilter(HeartbeatLogFilter())

# 3. 添加文件日志处理器 (保存到 logs/api_access.log)，最大 10MB，保留 5 个备份
file_handler = RotatingFileHandler("logs/api_access.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
access_logger.addHandler(file_handler)

# 4. 对 uvicorn.error (系统运行/启动日志) 也增加文件持久化
error_logger = logging.getLogger("uvicorn.error")
error_logger.addHandler(file_handler)

from src.agent.graph import FoodieGraph
from src.harness.audit_log import AuditLogger

app = FastAPI(title="SuperFoodie 超级吃货智能助手 API", version="1.0")

# 初始化共享状态机引擎
graph_engine = FoodieGraph()

# 静态对话历史临时索引 (session_id -> latest_state)
active_sessions = {}
IMAGE_PROXY_CACHE: dict[str, tuple[float, str, bytes]] = {}
IMAGE_PROXY_CACHE_TTL_SECONDS = 24 * 60 * 60

# Pydantic 数据模型定义

class StartRequest(BaseModel):
    user_id: str
    mode: str  # "home_cooking" | "dining_out"
    user_input: Optional[str] = ""
    current_disease: Optional[str] = ""
    location: Optional[str] = "我的位置"
    start_location: Optional[str] = None
    budget: Optional[float] = 150.0
    dining_people_count: Optional[int] = 1
    session_id: Optional[str] = None
    business_area_context: Optional[dict] = None
    selected_items: List[dict] = []

class StartResponse(BaseModel):
    session_id: str
    mode: str
    recommendations: Optional[List[dict]] = []
    search_timeline: Optional[List[dict]] = []
    recommendation_text: str
    health_explanation: str
    graph_rag_path: List[str]
    report_path: str
    image_url: Optional[str] = ""
    source: Optional[str] = ""

class InteractRequest(BaseModel):
    user_input: str

class InteractResponse(BaseModel):
    agent_response: str
    report_path: str

class SelectRequest(BaseModel):
    user_id: str
    item_name: str
    mode: str  # "home_cooking" | "dining_out"
    calories: Optional[int] = 300
    rating: Optional[float] = 4.5
    source_url: Optional[str] = ""

class SelectedItemsSyncRequest(BaseModel):
    user_id: str
    mode: str
    items: List[dict] = []

class BusinessAreaSearchRequest(BaseModel):
    query: Optional[str] = ""
    lng: Optional[float] = None
    lat: Optional[float] = None
    radius: Optional[int] = 5000
    city: Optional[str] = None
    user_id: Optional[str] = None
    explore_new: Optional[bool] = False
    debug: Optional[bool] = False


MAP_BUSINESS_AREA_CACHE = Path("logs/map_business_area_cache.json")
MAP_BUSINESS_AREA_CACHE_VERSION = "2026-06-21-v2"

# API 路由端点

@app.get("/api/foodie/system/status")
async def get_system_status():
    """
    健康诊断接口：检查大模型、Milvus 与高德地图 API 的配置与连接状态。
    同时提取由于接口或联网受限导致的社交平台数据抓取失败待办事件。
    """
    import json
    
    # 1. 检查 LLM 配置
    llm_key = os.getenv("LLM_API_KEY", "")
    llm_status = "online"
    if not llm_key or llm_key == "your_llm_api_key_here":
        llm_status = "offline_mock"
        
    # 2. 检查 Milvus 连接
    milvus_status = "connected"
    if graph_engine.bot.db.is_mock:
        milvus_status = "offline_mock_json"
        
    # 3. 检查高德 API 配置
    gaode_key = os.getenv("GAODE_API_KEY", "")
    gaode_status = "active"
    if not gaode_key or gaode_key == "your_gaode_api_key_here":
        gaode_status = "mock_estimation"
        
    # 4. 提取第三方抓取失败事件作为待办
    todo_list = []
    log_file = "logs/audit.jsonl"
    if os.path.exists(log_file):
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry.get("event") == "social_fetch_failed":
                        platform = entry.get("data", {}).get("platform", "第三方接口")
                        error = entry.get("data", {}).get("error", "获取超时")
                        todo_list.append(f"【待办修复】抓取 {platform} 失败 ({error})，请配置或检查网络 API 技能。")
        except Exception:
            pass
            
    # 去重
    todo_list = list(set(todo_list))
    if not todo_list and llm_status == "offline_mock":
        todo_list.append("【待办修复】大模型未配置 KEY，部分小红书/下厨房实时数据将默认降级到本地缓存。")

    return JSONResponse(content={
        "llm": llm_status,
        "milvus": milvus_status,
        "gaode": gaode_status,
        "platform_fetch_todo": todo_list
    })


@app.get("/api/foodie/map/config")
async def get_map_config():
    """Return public browser map configuration. Keep server-side Gaode keys out of the page."""
    js_key = os.getenv("GAODE_JS_API_KEY", "")
    security_code = os.getenv("GAODE_JS_SECURITY_CODE", "")
    enabled = bool(js_key) and js_key != "your_gaode_js_api_key_here"
    return JSONResponse(content={
        "provider": "amap",
        "enabled": enabled,
        "js_api_key": js_key if enabled else "",
        "security_code": security_code if enabled else "",
        "city": os.getenv("GAODE_DEFAULT_CITY", "北京"),
    })


def _normalize_amap_poi(poi: dict[str, Any], source_query: str, source_mode: str) -> Optional[dict[str, Any]]:
    location = poi.get("location") or ""
    if not location or "," not in location:
        return None
    try:
        lng_text, lat_text = location.split(",", 1)
        lng = float(lng_text)
        lat = float(lat_text)
    except ValueError:
        return None
    return {
        "id": poi.get("id") or "",
        "name": poi.get("name") or "",
        "type": poi.get("type") or "",
        "typecode": poi.get("typecode") or "",
        "address": poi.get("address") if isinstance(poi.get("address"), str) else "",
        "district": poi.get("adname") or "",
        "city": poi.get("cityname") or "",
        "lng": lng,
        "lat": lat,
        "distance": _safe_float(poi.get("distance")),
        "source_queries": [source_query] if source_query else [],
        "source_modes": [source_mode],
        "raw": poi,
    }


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _map_business_area_timeout() -> float:
    try:
        return max(2.0, min(float(os.getenv("GAODE_MAP_TIMEOUT_SECONDS", "5")), 8.0))
    except ValueError:
        return 5.0


def _map_business_area_result_limit() -> int:
    try:
        return max(8, min(int(os.getenv("MAP_BUSINESS_AREA_RESULT_LIMIT", "24")), 60))
    except ValueError:
        return 24


def _business_area_cache_ttl_seconds() -> int:
    try:
        return max(0, int(os.getenv("MAP_BUSINESS_AREA_CACHE_TTL_SECONDS", "86400")))
    except ValueError:
        return 86400


def _business_area_cache_key(
    query: str,
    city: str,
    radius: int,
    lng: Optional[float],
    lat: Optional[float],
    explore_new: bool,
) -> str:
    payload = {
        "version": MAP_BUSINESS_AREA_CACHE_VERSION,
        "query": query,
        "city": city,
        "radius": radius,
        "lng": round(float(lng), 4) if lng is not None else None,
        "lat": round(float(lat), 4) if lat is not None else None,
        "explore_new": bool(explore_new),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_business_area_cache() -> dict[str, Any]:
    if not MAP_BUSINESS_AREA_CACHE.exists():
        return {}
    try:
        return json.loads(MAP_BUSINESS_AREA_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_cached_business_area_result(cache_key: str) -> Optional[dict[str, Any]]:
    ttl = _business_area_cache_ttl_seconds()
    if ttl <= 0:
        return None
    entry = _read_business_area_cache().get(cache_key)
    if not isinstance(entry, dict):
        return None
    cached_at = float(entry.get("cached_at") or 0)
    if time.time() - cached_at > ttl:
        return None
    result = entry.get("result")
    return result if isinstance(result, dict) else None


def _set_cached_business_area_result(cache_key: str, result: dict[str, Any]) -> None:
    if _business_area_cache_ttl_seconds() <= 0:
        return
    try:
        MAP_BUSINESS_AREA_CACHE.parent.mkdir(parents=True, exist_ok=True)
        cache = _read_business_area_cache()
        cache[cache_key] = {"cached_at": time.time(), "result": result}
        if len(cache) > 80:
            items = sorted(
                cache.items(),
                key=lambda item: float(item[1].get("cached_at") or 0) if isinstance(item[1], dict) else 0,
                reverse=True,
            )
            cache = dict(items[:80])
        MAP_BUSINESS_AREA_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        return


def _business_area_public_response(full_result: dict[str, Any], debug: bool) -> dict[str, Any]:
    result_limit = _map_business_area_result_limit()
    ranked_results = full_result.get("ranked_results") or []
    content = {
        "provider": full_result.get("provider", "amap_web_service"),
        "query": full_result.get("query", ""),
        "city": full_result.get("city", ""),
        "radius": full_result.get("radius"),
        "center": full_result.get("center"),
        "raw_count": full_result.get("raw_count", 0),
        "unique_count": full_result.get("unique_count", 0),
        "eligible_count": full_result.get("eligible_count", 0),
        "excluded_count": full_result.get("excluded_count", 0),
        "collapsed_count": full_result.get("collapsed_count", 0),
        "duplicate_count": full_result.get("duplicate_count", 0),
        "explore_new": bool(full_result.get("explore_new")),
        "ranked_results": ranked_results if debug else ranked_results[:result_limit],
        "cache_hit": bool(full_result.get("cache_hit")),
        "debug_available": True,
        "debug": full_result.get("debug", {}) if debug else {
            "summary": full_result.get("debug", {}).get("summary", ""),
            "calls_count": len(full_result.get("debug", {}).get("calls", [])),
            "elapsed_ms": full_result.get("debug", {}).get("elapsed_ms"),
            "cache_hit": bool(full_result.get("cache_hit")),
            "result_limit": result_limit,
        },
        "raw_results": full_result.get("raw_results", []) if debug else [],
        "excluded_results": full_result.get("excluded_results", []) if debug else [],
    }
    return content


def _poi_rating(poi: dict[str, Any]) -> Optional[float]:
    raw = poi.get("raw") if isinstance(poi.get("raw"), dict) else {}
    biz_ext = raw.get("biz_ext") if isinstance(raw.get("biz_ext"), dict) else {}
    return _safe_float(biz_ext.get("rating"))


def _clean_business_name(name: str) -> str:
    cleaned = re.sub(r"（.*?）|\(.*?\)", "", name or "")
    cleaned = re.split(r"[-·]", cleaned, maxsplit=1)[0]
    cleaned = cleaned.replace("Mall", "").replace("MALL", "").replace("mall", "")
    for suffix in [
        "购物中心", "购物广场", "购物公园", "创意城", "体验城", "商业中心",
        "城市奥特莱斯", "奥特莱斯", "享馆", "广场", "商场", "商城",
        "坊", "店", "二馆", "一馆", "B座", "A座", "服务站", "体验中心", "生活馆"
    ]:
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix) + 1:
            cleaned = cleaned[: -len(suffix)]
    return cleaned.strip() or name


def _poi_key(poi: dict[str, Any]) -> str:
    name = "".join((poi.get("name") or "").lower().split())
    address = "".join((poi.get("address") or poi.get("district") or "").lower().split())[:24]
    location = f"{poi.get('lng', 0):.4f},{poi.get('lat', 0):.4f}"
    return f"{name}|{address}|{location}"


def _dedupe_pois(pois: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: dict[str, dict[str, Any]] = {}
    for poi in pois:
        key = _poi_key(poi)
        if key in merged:
            existing = merged[key]
            existing["source_queries"] = sorted(set(existing.get("source_queries", []) + poi.get("source_queries", [])))
            existing["source_modes"] = sorted(set(existing.get("source_modes", []) + poi.get("source_modes", [])))
            if existing.get("distance") is None or (
                poi.get("distance") is not None and poi["distance"] < existing["distance"]
            ):
                existing["distance"] = poi.get("distance")
            continue
        merged[key] = poi
    return list(merged.values()), max(0, len(pois) - len(merged))


def _canonical_business_name(name: str) -> str:
    canonical_markers = [
        "群光广场",
        "银泰创意城",
        "未来城购物公园",
        "流行视窗购物广场",
        "武商梦时代",
        "亚贸广场",
        "百港城",
        "泛悦",
        "维佳",
        "泛悦城",
        "楚河汉街",
        "水果湖步行街",
        "瑞景广场",
    ]
    for marker in canonical_markers:
        if marker in name:
            return marker
    root_aliases = {
        "泛悦": ["泛悦"],
        "维佳": ["维佳"],
    }
    for root, aliases in root_aliases.items():
        if any(alias in name for alias in aliases):
            return root
    cleaned = name
    for token in ["(", "（", "-", "·"]:
        if token in cleaned:
            cleaned = cleaned.split(token, 1)[0]
    suffixes = ["店", "二馆", "一馆", "B座", "A座", "服务站", "体验中心", "生活馆"]
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned.strip() or name


def _is_excluded_business_area_poi(poi: dict[str, Any]) -> tuple[bool, str]:
    text = f"{poi.get('name', '')} {poi.get('type', '')} {poi.get('address', '')}"
    type_text = poi.get("type") or ""
    typecode = str(poi.get("typecode") or "")
    raw = poi.get("raw") if isinstance(poi.get("raw"), dict) else {}
    biz_ext = raw.get("biz_ext") if isinstance(raw.get("biz_ext"), dict) else {}
    rating = _safe_float(biz_ext.get("rating"))
    hard_exclude_terms = [
        "餐饮服务",
        "住宿服务",
        "住宅区",
        "住宅项目",
        "公寓",
        "美寓",
        "生活服务",
        "体育休闲服务",
        "运动场馆",
        "体育场馆",
        "健身",
        "维修站",
        "服务站",
        "充电站",
        "专营店",
        "便利店",
        "超市",
        "超级市场",
        "综合市场",
        "小商品市场",
        "家具建材市场",
        "农副产品市场",
        "集贸市场",
        "菜市场",
        "建材",
        "五金",
        "水果",
        "生鲜",
        "咖啡",
        "咖啡厅",
        "药房",
        "药店",
        "医疗",
        "学校",
        "幼儿园",
        "公司企业",
        "政府机构",
        "汽车",
        "电动车",
        "用品店",
        "网购商城",
    ]
    soft_exclude_terms = [
        "商务住宅",
        "公司企业",
    ]
    allow_terms = [
        "购物中心",
        "购物广场",
        "广场",
        "商场",
        "商城",
        "百货",
        "商业街",
        "步行街",
        "综合体",
        "商圈",
        "天地",
        "银泰",
        "群光",
        "百港城",
        "泛悦城",
        "梦时代",
    ]
    trusted_business_names = [
        "群光广场",
        "银泰创意城",
        "未来城购物公园",
        "流行视窗购物广场",
        "武商梦时代",
        "亚贸广场",
        "百港城",
        "泛悦",
        "楚河汉街",
        "瑞景广场",
    ]
    residential_context_terms = ["公寓", "美寓", "住宅", "小区", "社区", "居民区", "底商", "花园", "名都"]
    school_context_terms = ["大学", "学院", "学校", "校区", "学府", "师范", "理工", "华中农业"]
    false_positive_names = [
        "百祥购物中心",
    ]
    if any(name in text for name in false_positive_names):
        hit = next(name for name in false_positive_names if name in text)
        return True, f"高德名称像商场但实测不适合作为商圈：{hit}"
    if rating is not None and rating <= 4.0:
        return True, f"高德评分不高于 4.0：{rating:g}"
    if any(term in text for term in hard_exclude_terms):
        hit = next(term for term in hard_exclude_terms if term in text)
        return True, f"不适合作为商圈候选：命中 {hit}"
    if "商务住宅" in type_text and "购物服务;商场" not in type_text and "购物服务;特色商业街" not in type_text:
        return True, "高德类型是商务住宅，且不是商场/商业街"
    commercial_type = "购物服务;商场" in type_text or "购物服务;特色商业街" in type_text
    trusted_name = any(name in text for name in trusted_business_names)
    residential_context = any(term in text for term in residential_context_terms)
    school_context = any(term in text for term in school_context_terms)
    street_like = "商业街" in text or "步行街" in text
    mall_like_name = any(term in (poi.get("name") or "") for term in ["购物中心", "商场", "商城", "广场"])
    if street_like and residential_context and not trusted_name:
        return True, "商业街/步行街位于住宅、社区或小区上下文，更像生活配套街"
    if street_like and school_context and not trusted_name:
        return True, "商业街/步行街位于学校或校区上下文，更像校园周边配套街"
    if residential_context and mall_like_name and not trusted_name:
        return True, "名称像商业体，但地址/类型显示为公寓、住宅或社区附属商业"
    if not commercial_type and not trusted_name:
        return True, "高德类型不是商场/商业街，且不是已知商业体"
    if any(term in text for term in allow_terms):
        return False, ""
    if any(term in text for term in soft_exclude_terms):
        hit = next(term for term in soft_exclude_terms if term in text)
        return True, f"不适合作为商圈候选：命中 {hit}"
    if typecode.startswith(("10", "12", "13", "14", "15", "16", "17", "18")):
        return True, f"高德类型 {typecode} 不属于商业综合体/商业街"
    if type_text and "购物服务" not in type_text:
        return True, "高德类型不是购物服务"
    return False, ""


def _representative_quality(poi: dict[str, Any]) -> float:
    text = f"{poi.get('name', '')} {poi.get('type', '')} {poi.get('address', '')}"
    score = 0.0
    for word in ["购物中心", "购物广场", "商场", "广场", "商业街", "步行街", "百货"]:
        if word in text:
            score += 10
    for word in ["普通商场", "购物中心"]:
        if word in text:
            score += 8
    for word in ["专营店", "便利店", "超市", "超级市场", "农副产品市场", "购物相关场所", "用品店"]:
        if word in text:
            score -= 15
    distance = poi.get("distance")
    if distance is not None:
        score += max(0.0, 3.0 - min(float(distance), 3000.0) / 1000.0)
    return score


def _collapse_business_entities(pois: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    grouped: dict[str, dict[str, Any]] = {}
    for poi in pois:
        canonical_name = _canonical_business_name(poi.get("name") or "")
        key = canonical_name
        rating = _poi_rating(poi)
        if key in grouped:
            existing = grouped[key]
            existing["source_queries"] = sorted(set(existing.get("source_queries", []) + poi.get("source_queries", [])))
            existing["source_modes"] = sorted(set(existing.get("source_modes", []) + poi.get("source_modes", [])))
            existing.setdefault("merged_names", [])
            if poi.get("name") and poi["name"] not in existing["merged_names"]:
                existing["merged_names"].append(poi["name"])
            if rating is not None:
                existing["rating"] = max(existing.get("rating") or 0, rating)
            nearest_distance = existing.get("distance")
            if existing.get("distance") is None or (
                poi.get("distance") is not None and poi["distance"] < existing["distance"]
            ):
                existing["distance"] = poi.get("distance")
                nearest_distance = poi.get("distance")
            if _representative_quality(poi) > existing.get("_representative_quality", -9999):
                for field in ["id", "type", "typecode", "address", "district", "city", "lng", "lat", "distance", "raw"]:
                    existing[field] = poi.get(field)
                existing["distance"] = nearest_distance
                existing["_representative_quality"] = _representative_quality(poi)
            continue
        grouped[key] = {
            **poi,
            "name": canonical_name,
            "merged_names": [poi.get("name") or canonical_name],
            "rating": rating,
            "_representative_quality": _representative_quality(poi),
        }
    return list(grouped.values()), max(0, len(pois) - len(grouped))


def _rank_business_area_poi(
    poi: dict[str, Any],
    user_query: str,
    radius: int,
    recent_area_names: Optional[set[str]] = None,
    explore_new: bool = False,
) -> dict[str, Any]:
    text = f"{poi.get('name', '')} {poi.get('type', '')} {poi.get('address', '')}"
    strong_terms = ["购物中心", "购物广场", "广场", "商场", "商城", "百货", "商业街", "步行街", "综合体", "商圈"]
    area_terms = ["购物", "中心", "天地", "万达", "大悦城", "银泰", "太古里", "新天地", "街区", "百港城", "群光"]
    shop_terms = ["餐厅", "饭店", "火锅", "烧烤", "小吃", "茶餐厅", "咖啡", "甜品", "面馆", "酒吧", "便利店"]
    score = 0.0
    reasons: list[str] = []
    strong_hits = [word for word in strong_terms if word in text]
    area_hits = [word for word in area_terms if word in text]
    shop_hits = [word for word in shop_terms if word in text]
    rating = poi.get("rating")
    if rating is None:
        rating = _poi_rating(poi)
    rating_score = 0.0
    if rating is not None:
        rating_score = max(0.0, min(50.0, (float(rating) - 4.0) * 50.0))
        score += rating_score
        reasons.append(f"评分 {float(rating):.1f}/5")
    if strong_hits:
        score += len(strong_hits) * 6
        reasons.append(f"商业体特征：{'、'.join(strong_hits)}")
    if area_hits:
        score += len(area_hits) * 3
    if user_query and user_query in text:
        score += 8
        reasons.append(f"匹配输入：{user_query}")
    typecode = str(poi.get("typecode") or "")
    if typecode.startswith("06"):
        score += 8
        reasons.append("高德类型：购物/商场")
    if typecode.startswith("05"):
        score -= 12
        reasons.append("更像餐饮单店，已降权")
    if shop_hits:
        score -= len(shop_hits) * 10
        reasons.append(f"具体店铺词降权：{'、'.join(shop_hits)}")
    distance = poi.get("distance")
    distance_score = 0.0
    if distance is not None and radius:
        distance_score = max(0.0, 35.0 * (1.0 - min(float(distance), float(radius)) / float(radius)))
        score += distance_score
        reasons.append(f"距离 {int(distance)}米")
    recent_area_names = recent_area_names or set()
    normalized_name = _clean_business_name(poi.get("name") or "")
    is_recent_area = any(
        normalized_name and (normalized_name in recent or recent in normalized_name)
        for recent in recent_area_names
    )
    novelty_score = 0.0
    if explore_new:
        novelty_score = -18.0 if is_recent_area else 12.0
        score += novelty_score
        reasons.append("探索新地图：近期去过，已降权" if is_recent_area else "探索新地图：近期少去，加权")
    if not reasons:
        reasons.append("按高德评分与距离排序")
    poi = {**poi}
    poi["rank_score"] = round(score, 2)
    poi["rating"] = rating
    poi["score_parts"] = {
        "rating": round(rating_score, 2),
        "distance": round(distance_score, 2),
        "novelty": round(novelty_score, 2),
    }
    poi["rank_reason"] = "；".join(reasons)
    return poi


async def _amap_get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    key = os.getenv("GAODE_API_KEY", "")
    if not key or key == "your_gaode_api_key_here":
        raise HTTPException(status_code=400, detail="GAODE_API_KEY is not configured")
    clean_params = {k: v for k, v in params.items() if v not in (None, "")}
    clean_params["key"] = key
    try:
        resp = await client.get(
            f"https://restapi.amap.com/v3/{path}",
            params=clean_params,
            timeout=_map_business_area_timeout(),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            return []
        return data.get("pois") or []
    except (httpx.HTTPError, httpx.TimeoutException):
        return []


async def _amap_geocode(client: httpx.AsyncClient, address: str, city: str) -> Optional[tuple[float, float]]:
    key = os.getenv("GAODE_API_KEY", "")
    if not address or not key or key == "your_gaode_api_key_here":
        return None
    try:
        resp = await client.get(
            "https://restapi.amap.com/v3/geocode/geo",
            params={"key": key, "address": address, "city": city},
            timeout=_map_business_area_timeout(),
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


@app.post("/api/foodie/map/business-areas")
async def search_business_areas(req: BusinessAreaSearchRequest):
    """
    Multi-recall Gaode business-area search.
    It keeps all deduped Gaode results and only ranks them, so known places are not silently hidden.
    """
    started_at = time.perf_counter()
    query = (req.query or "").strip()
    radius = max(1000, min(int(req.radius or 5000), 12000))
    city = (req.city or os.getenv("GAODE_DEFAULT_CITY", "北京")).strip()
    center: Optional[tuple[float, float]] = None
    raw_results: list[dict[str, Any]] = []
    debug_calls: list[dict[str, Any]] = []
    debug = bool(req.debug)
    cache_key = _business_area_cache_key(query, city, radius, req.lng, req.lat, bool(req.explore_new))
    cached = _get_cached_business_area_result(cache_key)
    if cached:
        cached = {**cached, "cache_hit": True}
        cached.setdefault("debug", {})
        cached["debug"]["elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
        return JSONResponse(content=_business_area_public_response(cached, debug))

    async with httpx.AsyncClient(follow_redirects=True) as client:
        if query:
            text_keywords = [
                query,
                f"{query} 购物",
                f"{query} 商场",
                f"{query} 购物中心",
                f"{query} 商业街",
            ]
            text_keywords = list(dict.fromkeys(text_keywords[:3]))
            text_tasks = [
                _amap_get(client, "place/text", {
                    "keywords": keyword,
                    "city": city,
                    "children": 1,
                    "offset": 15,
                    "page": 1,
                    "extensions": "base",
                })
                for keyword in text_keywords
            ]
            for keyword, pois in zip(text_keywords, await asyncio.gather(*text_tasks)):
                debug_calls.append({"mode": "text", "keyword": keyword, "count": len(pois)})
                for poi in pois:
                    normalized = _normalize_amap_poi(poi, keyword, "text")
                    if normalized:
                        raw_results.append(normalized)
            center = await _amap_geocode(client, query, city)

        if req.lng is not None and req.lat is not None:
            center = (float(req.lng), float(req.lat))
        elif center is None and raw_results:
            center = (raw_results[0]["lng"], raw_results[0]["lat"])

        if center:
            around_keywords = [
                "购物中心",
                "购物广场",
                "商场",
                "百货",
                "商业街",
                "步行街",
                "综合体",
                "商圈",
            ]
            if query:
                around_keywords = [query, f"{query} 购物", *around_keywords]
            around_keywords = list(dict.fromkeys(around_keywords[:5] if query else around_keywords[:4]))
            around_tasks = [
                _amap_get(client, "place/around", {
                    "location": f"{center[0]},{center[1]}",
                    "keywords": keyword,
                    "radius": radius,
                    "sortrule": "distance",
                    "offset": 15,
                    "page": 1,
                    "extensions": "base",
                })
                for keyword in around_keywords
            ]
            for keyword, pois in zip(around_keywords, await asyncio.gather(*around_tasks)):
                debug_calls.append({"mode": "around", "keyword": keyword, "count": len(pois)})
                for poi in pois:
                    normalized = _normalize_amap_poi(poi, keyword, "around")
                    if normalized:
                        raw_results.append(normalized)

    unique_results, duplicate_count = _dedupe_pois(raw_results)
    eligible_results: list[dict[str, Any]] = []
    excluded_results: list[dict[str, Any]] = []
    for poi in unique_results:
        is_excluded, reason = _is_excluded_business_area_poi(poi)
        if is_excluded:
            excluded_results.append({**poi, "exclude_reason": reason})
        else:
            eligible_results.append(poi)
    collapsed_results, collapsed_count = _collapse_business_entities(eligible_results)
    recent_area_names: set[str] = set()
    if req.explore_new and req.user_id:
        try:
            recent_area_names = {
                _clean_business_name(name)
                for name in graph_engine.bot.db.get_recent_footprints(req.user_id, days=90)
                if name
            }
        except Exception:
            recent_area_names = set()
    ranked_results = sorted(
        [
            _rank_business_area_poi(
                poi,
                query,
                radius,
                recent_area_names=recent_area_names,
                explore_new=bool(req.explore_new),
            )
            for poi in collapsed_results
        ],
        key=lambda item: item["rank_score"],
        reverse=True,
    )
    full_result = {
        "provider": "amap_web_service",
        "query": query,
        "city": city,
        "radius": radius,
        "center": {"lng": center[0], "lat": center[1]} if center else None,
        "raw_count": len(raw_results),
        "unique_count": len(unique_results),
        "eligible_count": len(eligible_results),
        "excluded_count": len(excluded_results),
        "collapsed_count": collapsed_count,
        "duplicate_count": duplicate_count,
        "explore_new": bool(req.explore_new),
        "ranked_results": ranked_results,
        "raw_results": unique_results,
        "excluded_results": excluded_results[:80],
        "cache_hit": False,
        "debug": {
            "calls": debug_calls,
            "summary": "Filtered commercial areas are ranked by Gaode rating, distance, commercial-area features and explore-new weight.",
            "ranking": "商业体筛选后按高德评分、距离和探索新地图权重排序；同一商业体会合并展示。",
            "recent_area_names": sorted(recent_area_names),
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
        },
    }
    _set_cached_business_area_result(cache_key, full_result)
    return JSONResponse(content=_business_area_public_response(full_result, debug))


@app.get("/api/foodie/image-proxy")
async def proxy_remote_image(url: str):
    """
    Proxy third-party recipe images through our backend.
    Some social CDNs reject direct browser hotlinking, so the UI should load this same-origin URL.
    """
    decoded_url = urllib.parse.unquote(url)
    parsed = urllib.parse.urlparse(decoded_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Unsupported image URL")

    cached = IMAGE_PROXY_CACHE.get(decoded_url)
    if cached and time.time() - cached[0] <= IMAGE_PROXY_CACHE_TTL_SECONDS:
        return Response(
            content=cached[2],
            media_type=cached[1],
            headers={"Cache-Control": "public, max-age=86400"},
        )

    host = parsed.netloc.lower()
    referer = f"{parsed.scheme}://{parsed.netloc}/"
    if "chuimg.com" in host or "xiachufang.com" in host:
        referer = "https://www.xiachufang.com/"
    elif "meishichina.com" in host:
        referer = "https://home.meishichina.com/"
    elif "xiaohongshu.com" in host or "xhscdn.com" in host:
        referer = "https://www.xiaohongshu.com/"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": referer,
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(decoded_url, headers=headers, timeout=15.0)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail="Remote image fetch failed")
        content_type = resp.headers.get("content-type", "image/jpeg")
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=415, detail="Remote URL is not an image")
        IMAGE_PROXY_CACHE[decoded_url] = (time.time(), content_type, resp.content)
        return Response(
            content=resp.content,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image proxy failed: {type(exc).__name__}")


@app.post("/api/foodie/start", response_model=StartResponse)
async def start_foodie_flow(req: StartRequest):
    """
    初始化吃货助理流，前置进行长期偏好提取、健康图谱校验并启动状态机。
    """
    session_id = req.session_id or f"session-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    
    # 构建初始状态
    initial_state = {
        "session_id": session_id,
        "user_id": req.user_id,
        "mode": req.mode,
        "current_disease": req.current_disease,
        "location": req.location,
        "start_location": req.start_location,
        "budget": req.budget,
        "dining_people_count": req.dining_people_count or 1,
        "business_area_context": req.business_area_context,
        "selected_items": req.selected_items or [],
        "user_input": req.user_input if req.user_input is not None else (
            "推荐今日特色菜" if req.mode == "home_cooking" else "推荐附近的特色餐馆"
        )
    }
    
    # 实例化审计日志记录器
    logger = AuditLogger(session_id)
    logger.log("session_start", {"req": req.dict()})
    
    try:
        # 运行 LangGraph / 模拟状态机
        final_state = await graph_engine.run(initial_state)
        
        # 存储会话快照
        active_sessions[session_id] = final_state
        
        logger.log("session_initialized", {
            "mode": final_state.get("mode"),
            "health_explanation": final_state.get("health_explanation"),
            "report_path": final_state.get("report_path")
        })
        
        return StartResponse(
            session_id=session_id,
            mode=final_state.get("mode"),
            recommendations=final_state.get("recommendations", []),
            search_timeline=final_state.get("search_timeline", []),
            recommendation_text=final_state.get("recommendation_text", "无法生成推荐"),
            health_explanation=final_state.get("health_explanation", ""),
            graph_rag_path=final_state.get("graph_rag_path", []),
            report_path=final_state.get("report_path", ""),
            image_url=final_state.get("image_url", ""),
            source=final_state.get("source", "")
        )
    except Exception as e:
        logger.log("session_start_failed", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"启动吃货流程失败: {str(e)}")


@app.post("/api/foodie/{session_id}/interact", response_model=InteractResponse)
async def interact_foodie_flow(session_id: str, req: InteractRequest):
    """
    与吃货助手进行多轮对话，支持食材平替、物态追问、娱乐推荐等深度问答。
    """
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
        
    last_state = active_sessions[session_id]
    logger = AuditLogger(session_id)
    logger.log("user_interaction", {"user_input": req.user_input})
    logger.log("thought_step", {"message": "正在将您的追问录入 Milvus 吃货历史记忆库..."})
    
    try:
        # 将用户对话记录进 Milvus 历史
        graph_engine.bot.db.insert_chat(session_id, "user", req.user_input)
        
        messages = [
            {"role": "user", "content": req.user_input}
        ]
        
        logger.log("thought_step", {"message": "正在调用大模型分析意图并生成回复..."})
        # 获取 Chatbot 响应
        response_text = await graph_engine.bot.call_llm(messages, disable_thinking=True)
        
        logger.log("thought_step", {"message": "正在对回复内容进行多轮 GraphRAG 安全防线碰撞检测..."})
        # 实时进行多轮 Graph-RAG 安全碰撞（红色警告机制，由用户自行研判）
        disease = last_state.get("current_disease", "")
        red_warning = ""
        if disease and disease != "正常健康":
            # 痛风喝啤酒/喝酒特别柔性警告判定
            is_gout_alcohol = (disease == "痛风" and any(w in req.user_input for w in ["酒", "啤酒", "喝酒"]))
            
            if is_gout_alcohol:
                red_warning = f"<div style='border: 1px solid #E74C3C; background: rgba(231, 76, 60, 0.12); padding: 15px; border-radius: 10px; margin-bottom: 15px;'><span style='color:#EC7063; font-weight:bold;'>🚨【健康安全红色警示】</span><br/><span style='font-size: 13px; color:#F5B041;'>检测到您当前处于痛风状态，啤酒为高嘌呤食物，过量饮用可能诱发关节不适，但非一点不能喝，请您自行斟酌！若有饮酒诉求，可适量考虑烧酒平替，啤酒也请尽量控制在极少分量。</span></div>\n\n"
            else:
                # 检测用户提问中是否涉嫌冲突的敏感食物词
                sensitive_foods = ["啤酒", "海鲜", "牛肉", "鸭肉", "毛肚"]
                for food in sensitive_foods:
                    if food in req.user_input:
                        # 触发 Graph-RAG 禁忌检测
                        safety_res = graph_engine.bot.retriever.query_diet_safety(disease, food)
                        if not safety_res["safety"]:
                            # 冲突发生，拼装高质感红色高亮警示框，前端会自动将其渲染出来
                            red_warning = f"<div style='border: 1px solid #E74C3C; background: rgba(231, 76, 60, 0.12); padding: 15px; border-radius: 10px; margin-bottom: 15px;'><span style='color:#EC7063; font-weight:bold;'>🚨【健康安全红色警示】</span><br/><span style='font-size: 13px; color:#F5B041;'>检测到您处于【{disease}】状况。Obsidian 健康图谱表明，您询问的食材【{food}】与当前病理冲突（{safety_res['reason']}），饮用/食用过量可能会诱发身体不适。此提示仅供安全警示参考，请您根据身体现状自行研判决策！如有需要，可参考平替方案。</span></div>\n\n"
                            break
        
        if red_warning:
            response_text = red_warning + response_text
            
        # 将回复记录进 Milvus 历史
        graph_engine.bot.db.insert_chat(session_id, "assistant", response_text)
        
        logger.log("thought_step", {"message": "对话处理完成，正在为您呈现回复..."})
        logger.log("agent_response_success", {"response": response_text})
        
        return InteractResponse(
            agent_response=response_text,
            report_path=last_state.get("report_path", "")
        )
    except Exception as e:
        logger.log("interaction_failed", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"交互处理失败: {str(e)}")


def is_dirty_data(item_name: str) -> bool:
    """
    脏数据检查：如果包含长疑问句、明显的指令性语气词、或是空和过长的字符串，判定为脏数据。
    """
    if not item_name or len(item_name.strip()) == 0:
        return True
    
    # 限制菜名长度不应过长（一般正常菜名不会超过20个字符）
    if len(item_name) > 20:
        return True
        
    dirty_keywords = [
        "怎么", "如何", "做", "吃啥", "吃什么", "推荐", "我想吃", "帮我", "去哪", "做法", 
        "食谱", "菜谱", "有啥", "哪些", "help", "select"
    ]
    name_lower = item_name.lower()
    for kw in dirty_keywords:
        if kw in name_lower:
            return True
            
    # 如果包含句号、问号、惊叹号等句子结束符，判定为长句脏数据
    if any(char in item_name for char in [",", "，", ".", "。", "?", "？", "!", "！", "\n", "\r"]):
        return True
        
    return False


def _compact_business_area_context(context: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    allowed = [
        "name", "address", "type", "typecode", "rating", "distance", "radius",
        "lng", "lat", "center", "rank_score", "rank_reason", "merged_names",
        "score_parts", "explore_new"
    ]
    return {key: context.get(key) for key in allowed if context.get(key) not in (None, "", [])}


@app.post("/api/foodie/{session_id}/select")
async def select_foodie_item(session_id: str, req: SelectRequest):
    """
    接收用户在前端选定的最终菜品/餐厅，进行脏数据净化拦截，并写入 Milvus 饮食足迹库。
    """
    logger = AuditLogger(session_id)
    
    # 进行脏数据校验拦截
    if is_dirty_data(req.item_name):
        print("\n" + "="*80, flush=True)
        print(f"【脏数据拦截警告】拒绝写入非规范足迹词：'{req.item_name}'", flush=True)
        print("="*80 + "\n", flush=True)
        logger.log("footprint_write_blocked", {"item_name": req.item_name, "reason": "判定为对话长句或包含疑问词的脏数据"})
        raise HTTPException(status_code=400, detail="非法的菜品/商户名，请勿输入疑问长句作为足迹写入。")
        
    try:
        if req.mode == "home_cooking":
            # 存入 Milvus 做菜足迹
            graph_engine.bot.db.insert_footprint(
                user_id=req.user_id,
                item_name=req.item_name,
                item_type="recipe",
                metadata={"calories": req.calories}
            )
        else:
            # 存入 Milvus 探店餐厅足迹
            graph_engine.bot.db.insert_footprint(
                user_id=req.user_id,
                item_name=req.item_name,
                item_type="restaurant",
                metadata={"rating": req.rating}
            )
            
        logger.log("user_footprint_saved", {"user_id": req.user_id, "item_name": req.item_name, "mode": req.mode})
        
        # 记录用户最终选中项目，以便在导出 PDF 报告时重新渲染报告
        if session_id in active_sessions:
            recs = active_sessions[session_id].get("recommendations", [])
            selected_rec = None
            for rec in recs:
                if rec.get("name") == req.item_name and (not req.source_url or rec.get("source_url", "") == req.source_url):
                    selected_rec = rec
                    break
            if selected_rec is None:
                selected_rec = {
                    "name": req.item_name,
                    "calories": req.calories,
                    "rating": req.rating,
                    "source_url": req.source_url,
                }
            if req.mode == "home_cooking":
                selected_items = active_sessions[session_id].setdefault("selected_items", [])
                if not any(item.get("name") == selected_rec.get("name") and item.get("source_url", "") == selected_rec.get("source_url", "") for item in selected_items):
                    selected_items.append(selected_rec)
            else:
                active_sessions[session_id]["selected_recommendation"] = selected_rec
            active_sessions[session_id]["selected_item"] = req.item_name
        
        print("\n" + "="*80, flush=True)
        print(f"【Milvus 写入成功】正式记录用户最终就餐选择：", flush=True)
        print(f"  用户: {req.user_id} | 品类: {req.mode} | 品名: '{req.item_name}'", flush=True)
        print("="*80 + "\n", flush=True)
        
        return JSONResponse(content={
            "status": "success", 
            "message": f"饮食足迹【{req.item_name}】已成功存入 Milvus 偏好库。"
        })
    except Exception as e:
        logger.log("footprint_save_failed", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"保存就餐足迹失败: {str(e)}")


@app.post("/api/foodie/{session_id}/selected-items")
async def sync_selected_items(session_id: str, req: SelectedItemsSyncRequest):
    """Synchronize current frontend picked items before report export."""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="会话已过期，无法同步备选菜单")
    state = active_sessions[session_id]
    recs = state.get("recommendations", [])
    synced = []
    for incoming in req.items or []:
        name = incoming.get("name") or incoming.get("store_name") or ""
        source_url = incoming.get("source_url", "")
        matched = None
        for rec in recs:
            if rec.get("name") == name and (not source_url or rec.get("source_url", "") == source_url):
                matched = rec
                break
        synced.append(matched or incoming)
    state["selected_items"] = synced
    if req.mode == "dining_out" and synced:
        state["selected_recommendation"] = synced[0]
        state["selected_item"] = synced[0].get("name") or synced[0].get("store_name")
    elif req.mode == "home_cooking":
        state["selected_item"] = synced[-1].get("name") if synced else ""
    return JSONResponse(content={"status": "success", "count": len(synced)})


@app.get("/api/foodie/{session_id}/report")
async def get_foodie_report(session_id: str):
    """
    获取就餐决策报告。支持下载精美渲染的 PDF 报告。
    [延迟路线规划重构]：在导出 PDF 阶段，根据用户选定的最终商户，
    实时向高德地图 API 请求路线规划，并更新状态数据后，重新渲染 PDF 报告返回。
    """
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="会话已过期，报告无法找回")
        
    state = active_sessions[session_id]

    if state.get("mode") == "home_cooking":
        selected_items = state.get("selected_items", [])
        first_image = ""
        for item in selected_items:
            first_image = (
                item.get("image_url")
                or (item.get("source_image_urls") or [""])[0]
                or (item.get("raw_source_image_urls") or [""])[0]
            )
            if first_image:
                break

        pdf_path = f"docs/reports/{session_id}_report.pdf"
        report_data = {
            "user_id": state.get("user_id"),
            "mode": "home_cooking",
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "recommendation_text": "",
            "selected_recommendations": selected_items,
            "graph_rag_path": state.get("graph_rag_path", []),
            "health_explanation": state.get("health_explanation"),
            "image_url": first_image,
        }
        final_path = graph_engine.report_gen.generate_report(session_id, report_data, pdf_path)
        state["report_path"] = final_path
    
    if state.get("mode") == "dining_out":
        selected_item = state.get("selected_item")
        recs = state.get("recommendations", [])
        selected_rec = None
        
        # 用户未点击选择任何具体商户时，默认采用列表第 1 家
        if not selected_item and recs:
            selected_item = recs[0]["name"]
            
        if selected_item:
            for rec in recs:
                if rec.get("name") == selected_item or rec.get("store_name") == selected_item:
                    selected_rec = rec
                    break
            if selected_rec is None and recs:
                selected_rec = recs[0]
            
            if selected_rec:
                state["recommendation_text"] = selected_rec.get("recommendation_text", state.get("recommendation_text"))
                state["image_url"] = selected_rec.get("image_url", "")
                state["source"] = selected_rec.get("source", "大众点评")
                    
            pdf_path = f"docs/reports/{session_id}_report.pdf"
            report_data = {
                "user_id": state.get("user_id"),
                "mode": "dining_out",
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "recommendation_text": state.get("recommendation_text"),
                "graph_rag_path": state.get("graph_rag_path", []),
                "health_explanation": state.get("health_explanation"),
                "image_url": state.get("image_url"),
                "selected_recommendation": selected_rec or {},
                "after_meal_places": (selected_rec or {}).get("after_meal_places", []),
                "taste_query": state.get("user_input", "")
            }
            final_path = graph_engine.report_gen.generate_report(session_id, report_data, pdf_path)
            state["report_path"] = final_path

    report_path = state.get("report_path")
    if not report_path or not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="报告文件尚未生成或已被移除")

    if state.get("mode") == "dining_out" and not state.get("business_area_footprint_saved"):
        business_area = _compact_business_area_context(state.get("business_area_context"))
        area_name = business_area.get("name") or state.get("location")
        if area_name:
            try:
                graph_engine.bot.db.insert_footprint(
                    user_id=state.get("user_id", "anonymous"),
                    item_name=str(area_name)[:80],
                    item_type="business_area",
                    metadata={
                        "source": "pdf_export",
                        "session_id": session_id,
                        "location": state.get("location"),
                        "selected_item": state.get("selected_item"),
                        "food_query": state.get("user_input"),
                        "business_area": business_area,
                    },
                )
                state["business_area_footprint_saved"] = True
            except Exception as exc:
                AuditLogger(session_id).log("business_area_footprint_save_failed", {"error": str(exc)})
        
    # 判断生成的是 PDF 还是降级后的 Markdown，返回对应的媒体类型
    media_type = "application/pdf" if report_path.endswith(".pdf") else "text/markdown"
    filename = f"SuperFoodie_Report_{session_id}" + (".pdf" if report_path.endswith(".pdf") else ".md")
    
    return FileResponse(
        path=report_path,
        media_type=media_type,
        filename=filename
    )


@app.get("/api/foodie/audit/logs")
async def get_audit_logs(session_id: Optional[str] = None):
    """
    可观测性端点：获取追加写审计日志流，支持按 session_id 过滤。
    """
    log_file = "logs/audit.jsonl"
    if not os.path.exists(log_file):
        return JSONResponse(content=[])
        
    logs = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if session_id:
                        if entry.get("session_id") == session_id:
                            logs.append(entry)
                    else:
                        logs.append(entry)
                except Exception:
                    continue
        return JSONResponse(content=logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取审计日志失败: {str(e)}")


# 挂载单页面 UI 静态目录
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
else:
    print(f"【FastAPI 警告】未找到静态文件目录 '{static_dir}'，请确保 index.html 存在。")
