# -*- coding: utf-8 -*-
import os
import httpx
import json
import asyncio
import sys
import urllib.parse
import re
import hashlib
import time
from pathlib import Path
from src.config.env import load_project_env
from src.tools.graph_rag_retriever import GraphRAGRetriever
from src.memory.milvus_manager import MilvusManager
from src.tools.food_search import search_recipes_online, search_xhs_recipes_only, search_recipe_images_online, search_nearby_restaurants, search_xhs_dining_plan, search_gaode_restaurants, enrich_restaurants_with_gaode, enrich_restaurants_with_xhs_notes, search_gaode_entertainment, get_trending_foods, probe_xhs_available, safe_print, _search_recipes_from_local_cache
from src.tools.location_map import LocationMap
from src.harness.audit_log import AuditLogger

load_project_env()

AUTO_RECIPE_POOL = [
    {"name": "清蒸鲈鱼", "people": "single", "slot": "单人主菜"},
    {"name": "番茄炒蛋", "people": "single", "slot": "单人主菜"},
    {"name": "香菇菜心", "people": "single", "slot": "单人主菜"},
    {"name": "小炒黄牛肉", "people": "multi", "slot": "荤菜"},
    {"name": "辣子鸡", "people": "multi", "slot": "荤菜"},
    {"name": "蒜蓉粉丝虾", "people": "multi", "slot": "荤菜"},
    {"name": "香菇菜心", "people": "multi", "slot": "素菜"},
    {"name": "蚝油生菜", "people": "multi", "slot": "素菜"},
    {"name": "手撕包菜", "people": "multi", "slot": "素菜"},
    {"name": "番茄豆腐汤", "people": "multi", "slot": "汤"},
    {"name": "紫菜蛋花汤", "people": "multi", "slot": "汤"},
    {"name": "冬瓜丸子汤", "people": "multi", "slot": "汤"},
]

PLANNED_SLOT_QUERIES = {
    "下饭荤菜": "荤菜",
    "家常素菜": "素菜",
    "家常汤": "汤",
}

HOME_RECIPE_PLAN_CACHE_PATH = Path("logs/home_recipe_plan_cache.json")

def rewrite_recipe_search_query(query: str, dining_people_count: int, disease: str = "") -> str:
    """Turn vague taste preferences into concrete recipe queries before web search."""
    text = (query or "").strip()
    if not text:
        return text
    ingredient_to_dish = {
        "牛肉": "小炒黄牛肉",
        "鸡肉": "辣子鸡",
        "猪肉": "水煮肉片",
        "鱼": "清蒸鲈鱼",
        "虾": "蒜蓉粉丝虾",
        "排骨": "土豆烧排骨",
        "土豆": "酸辣土豆丝",
        "白菜": "醋溜白菜",
        "青菜": "香菇菜心",
        "豆腐": "麻婆豆腐",
        "鸡蛋": "番茄炒蛋",
    }
    if text in ingredient_to_dish:
        return ingredient_to_dish[text]
    normalized = text.replace("一点", "").replace("一些", "").replace("点", "")
    if any(word in normalized for word in ["辣", "重口", "下饭", "开胃"]):
        if disease and any(word in disease for word in ["感冒", "咳嗽", "上火"]):
            return "青椒鸡丁"
        return "辣子鸡"
    if any(word in normalized for word in ["清淡", "不油", "少油", "健康"]):
        return "清蒸鲈鱼"
    if any(word in normalized for word in ["简单", "快手", "懒人", "省事"]):
        return "番茄炒蛋"
    if any(word in normalized for word in ["素", "蔬菜", "青菜"]):
        return "香菇菜心"
    if "汤" in normalized:
        return "番茄豆腐汤"
    return text

def should_expand_recipe_search_queries(query: str) -> bool:
    """Only expand vague taste/ingredient requests into multiple web searches."""
    text = (query or "").strip()
    if not text:
        return False
    vague_markers = [
        "随便", "推荐", "不知道", "吃什么", "来点", "想吃", "一点", "口味",
        "清淡", "重口", "下饭", "健康", "快手", "懒人", "简单", "都行",
    ]
    if any(marker in text for marker in vague_markers):
        return True
    broad_ingredients = [
        "牛肉", "鸡肉", "猪肉", "鱼", "虾", "排骨", "土豆", "白菜", "青菜", "豆腐", "鸡蛋",
    ]
    if text in broad_ingredients:
        return True
    return False

def proxied_image_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    if not url.startswith(("http://", "https://")):
        return url
    return f"/api/foodie/image-proxy?url={urllib.parse.quote(url, safe='')}"

def choose_auto_recipe_query(dining_people_count: int, disease: str, recent_names: list[str]) -> str:
    people_type = "single" if dining_people_count <= 1 else "multi"
    blocked_keywords = []
    if disease and disease != "正常健康":
        if "痛风" in disease:
            blocked_keywords.extend(["虾", "海鲜", "牛肉", "啤酒"])
        if "感冒" in disease or "咳嗽" in disease:
            blocked_keywords.extend(["辣子", "香辣", "黄牛肉", "牛肉"])

    candidates = [
        item for item in AUTO_RECIPE_POOL
        if item["people"] == people_type
        and not any(word in item["name"] for word in blocked_keywords)
        and not any(item["name"] in recent or recent in item["name"] for recent in recent_names)
    ]
    if not candidates:
        candidates = [
            item for item in AUTO_RECIPE_POOL
            if item["people"] == people_type
            and not any(word in item["name"] for word in blocked_keywords)
        ]
    if not candidates:
        return "清蒸鲈鱼" if people_type == "single" else "椰子鸡"
    return candidates[0]["name"]

def choose_auto_menu_queries(dining_people_count: int, disease: str, recent_names: list[str]) -> list[dict]:
    if dining_people_count <= 1:
        blocked_keywords = []
        if disease and disease != "正常健康":
            if "痛风" in disease:
                blocked_keywords.extend(["虾", "海鲜", "牛肉", "啤酒"])
            if "感冒" in disease or "咳嗽" in disease:
                blocked_keywords.extend(["辣子", "香辣", "黄牛肉", "牛肉"])
        candidates = [
            item for item in AUTO_RECIPE_POOL
            if item["people"] == "single"
            and not any(word in item["name"] for word in blocked_keywords)
            and not any(item["name"] in recent or recent in item["name"] for recent in recent_names)
        ]
        if len(candidates) < 3:
            existing = {item["name"] for item in candidates}
            candidates.extend([
                item for item in AUTO_RECIPE_POOL
                if item["people"] == "single"
                and item["name"] not in existing
                and not any(word in item["name"] for word in blocked_keywords)
            ])
        return [{"slot": "单人主菜", "query": item["name"]} for item in candidates[:3]]

    blocked_keywords = []
    if disease and disease != "正常健康":
        if "痛风" in disease:
            blocked_keywords.extend(["虾", "海鲜", "牛肉", "啤酒"])
        if "感冒" in disease or "咳嗽" in disease:
            blocked_keywords.extend(["辣子", "香辣", "黄牛肉", "牛肉"])

    menu = []
    for slot in ["荤菜", "素菜", "汤"]:
        slot_candidates = [
            item for item in AUTO_RECIPE_POOL
            if item["people"] == "multi"
            and item["slot"] == slot
            and not any(word in item["name"] for word in blocked_keywords)
            and not any(item["name"] in recent or recent in item["name"] for recent in recent_names)
        ]
        if not slot_candidates:
            slot_candidates = [
                item for item in AUTO_RECIPE_POOL
                if item["people"] == "multi"
                and item["slot"] == slot
                and not any(word in item["name"] for word in blocked_keywords)
            ]
        if slot_candidates:
            menu.append({"slot": slot, "query": slot_candidates[0]["name"]})
    return menu or [{"slot": "荤菜", "query": "椰子鸡"}]

def infer_menu_slot(query: str, dining_people_count: int) -> str:
    if dining_people_count <= 1:
        return "单人主菜"
    query = query or ""
    if query in PLANNED_SLOT_QUERIES:
        return PLANNED_SLOT_QUERIES[query]
    if any(word in query for word in ["素", "青菜", "菜心", "生菜", "包菜", "蔬菜"]):
        return "素菜"
    if any(word in query for word in ["汤", "羹"]):
        return "汤"
    if any(word in query for word in ["荤", "肉", "鸡", "牛", "鱼", "虾", "排骨"]):
        return "荤菜"
    return "指定菜品"

def is_planned_slot_query(query: str, dining_people_count: int) -> bool:
    return dining_people_count > 1 and (query or "") in PLANNED_SLOT_QUERIES

def is_invalid_recipe_candidate_name(name: str) -> bool:
    text = (name or "").strip()
    if not text or len(text) > 18:
        return True
    if "?" in text or "�" in text:
        return True
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
        return True
    generic_names = {
        "家常菜", "三道家常菜", "推荐三道家常菜", "今日特色菜", "推荐今日特色菜",
        "下饭菜", "清淡菜", "快手菜", "素菜", "荤菜", "汤",
    }
    if text in generic_names:
        return True
    generic_markers = ["推荐", "不知道", "随便", "吃什么", "几个菜", "三道", "菜谱"]
    return any(marker in text for marker in generic_markers)

def restaurant_center_from_gaode(rest: dict) -> tuple[float, float] | None:
    raw_poi = rest.get("raw_poi") or rest.get("gaode_raw_poi") or {}
    location = raw_poi.get("location") or rest.get("location") or ""
    if not isinstance(location, str) or "," not in location:
        return None
    try:
        lng_text, lat_text = location.split(",", 1)
        return float(lng_text), float(lat_text)
    except (TypeError, ValueError):
        return None

class FoodieChatbot:
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY")
        self.api_base = os.getenv("LLM_API_BASE", "https://api.bianxie.ai/v1")
        self.model = os.getenv("LLM_MODEL", "deepseek-chat")
        
        # 依赖初始化
        self.retriever = GraphRAGRetriever("obsidian_vault")
        self.db = MilvusManager()
        self.map_client = LocationMap()
        
        # 判定是否启用本地离线规则引擎兜底
        self.is_offline = not bool(self.api_key) or self.api_key == "your_llm_api_key_here"
        if self.is_offline:
            safe_print("【Chatbot 警告】未配置有效的 LLM_API_KEY，启用本地吃货规则推理引擎。", flush=True)

    async def get_embedding(self, text: str) -> list[float]:
        """获取文本的 1536 维向量表示（支持离线 Mock 降级）"""
        if self.is_offline:
            return [0.0] * 1536
            
        retries = 3
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient() as client:
                    # 兼容百炼与主流 OpenAI 格式的 embeddings 接口
                    resp = await client.post(
                        f"{self.api_base}/embeddings",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={
                            "input": text,
                            "model": "text-embedding-v1"
                        },
                        timeout=5.0
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return data["data"][0]["embedding"]
                    else:
                        safe_print(f"【Embedding 错误】接口状态码 {resp.status_code}，返回: {resp.text}", flush=True)
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                safe_print(f"【Embedding 尝试 {attempt+1}/{retries} 失败】({e})。正在重试...", flush=True)
                if attempt < retries - 1:
                    await asyncio.sleep(1.0)
        return [0.0] * 1536

    async def call_llm(
        self,
        messages: list[dict],
        temperature: float = 0.5,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
        disable_thinking: bool = True,
    ) -> str:
        """调用大模型，在无 Key 时自动切换为 Mock 回复"""
        last_message = messages[-1]["content"]
        
        safe_print("\n" + "="*80, flush=True)
        safe_print(f"【LLM 真实调用】请求大模型进行对话推理...", flush=True)
        safe_print(f"  模型: {self.model}", flush=True)
        safe_print(f"  用户消息: '{last_message}'", flush=True)
        safe_print("="*80 + "\n", flush=True)
        
        if self.is_offline:
            return self._mock_llm_response(last_message)

        retries = 3
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient() as client:
                    body = {
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                    }
                    if max_tokens:
                        body["max_tokens"] = max_tokens
                    if disable_thinking:
                        body["enable_thinking"] = False
                    response = await client.post(
                        f"{self.api_base}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=body,
                        timeout=timeout_seconds or float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
                    )
                    if response.status_code >= 400 and disable_thinking and "enable_thinking" in body:
                        body.pop("enable_thinking", None)
                        response = await client.post(
                            f"{self.api_base}/chat/completions",
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            json=body,
                            timeout=timeout_seconds or float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
                        )
                    response.raise_for_status()
                    data = response.json()
                    reply = data["choices"][0]["message"]["content"]
                    
                    safe_print("\n" + "="*80, flush=True)
                    safe_print(f"【LLM 推理成功】回复内容:", flush=True)
                    safe_print(reply, flush=True)
                    safe_print("="*80 + "\n", flush=True)
                    
                    return reply
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                safe_print(f"【LLM 尝试 {attempt+1}/{retries} 失败】({e})。正在重试...", flush=True)
                if attempt < retries - 1:
                    await asyncio.sleep(1.5)
                else:
                    safe_print(f"【LLM 最终失败】启用本地 Mock 兜底。", flush=True)
                    return self._mock_llm_response(last_message)

    def _mock_llm_response(self, user_input: str) -> str:
        """基于简单规则的离线吃货知识问答系统"""
        user_input_lower = user_input.lower()
        
        if "生抽" in user_input_lower:
            return "【物理平替方案】生抽没有了，可以使用少量【老抽 + 适量食盐 + 极少白糖】兑入清水代替。老抽上色，盐起咸味，糖提鲜味。\n来源：下厨房"
        elif "粘锅" in user_input_lower:
            return "【主厨秘籍】要防止粘锅，请使用【热锅冷油】法：大火将炒锅烧干至锅底微微浮现【一层薄薄白烟】时，转中火，倒入冷油，并在油中撒入少许【食盐】，即可形成良好的物理防粘涂层。\n来源：小红书"
        elif "毛豆" in user_input_lower:
            return "【物态判定】煮毛豆时，大火烧水，待【毛豆全部漂浮在水面】且颜色呈翠绿时，说明已经彻底煮熟。此时应迅速将其捞出并投入【冰水】中冷水激凉，可保持表皮爽脆翠绿。\n来源：下厨房"
        elif "怎么做" in user_input_lower or "做菜" in user_input_lower:
            return "建议选择经典的家常菜，比如【白切鸡】。如果鸡肉是冷冻过的，请在冷水下锅焯水时加入【生姜、大葱和料酒】以彻底去除冷冻肉腥味；如果是新鲜的，则只需沸水直接浸烫以保持鸡肉天然甘甜。\n来源：小红书"
        
        return f"听起来很好吃！建议您根据最近热门美食或者找店诉求，让我为您做图文规划。"

    async def optimize_recipe_search_queries(self, user_query: str, disease: str, dining_people_count: int, logger: AuditLogger) -> list[str]:
        """
        Use the LLM to turn vague home-cooking intent into 3 concrete searchable dish queries.
        Falls back to local rules when the model is unavailable or returns invalid JSON.
        """
        original_query = (user_query or "").strip() or "不知道吃什么，推荐今天适合做的家常菜"

        fallback_query = rewrite_recipe_search_query(original_query, dining_people_count, disease)
        fallback_queries = [fallback_query]
        if self.is_offline:
            return fallback_queries

        prompt = f"""
你是一个中文家常菜检索词优化器。请把用户的做菜需求快速改写成 3 个适合去小红书/菜谱网站搜索的具体菜名或菜谱关键词。

要求：
1. 不要直接使用“辣一点的、清淡点、随便、下饭”等泛口味词。
2. 每个结果必须是具体菜名或明确菜谱关键词，例如“辣子鸡”“青椒鸡丁”“清蒸鲈鱼”。
3. 三个结果之间要有差异，不要同义重复。
4. 要考虑人数和身体状态，但不要过度养生化。
5. 不要深度思考，不要输出推理过程，只做快速决策。
6. 只返回 JSON，不要解释。

用户原始需求：{original_query}
就餐人数：{dining_people_count}
身体状态：{disease or "正常健康"}

返回格式：
{{"search_queries":["具体检索词1","具体检索词2","具体检索词3"],"reason":"一句话说明为什么这样搜"}}
""".strip()
        try:
            reply = await self.call_llm([
                {"role": "system", "content": "你只输出合法 JSON，不输出 Markdown，不输出思考过程。"},
                {"role": "user", "content": prompt},
            ], temperature=0.2, timeout_seconds=float(os.getenv("LLM_FAST_TIMEOUT_SECONDS", "60")), max_tokens=260, disable_thinking=True)
            match = re.search(r"\{.*\}", reply, flags=re.DOTALL)
            payload = json.loads(match.group(0) if match else reply)
            raw_queries = payload.get("search_queries") or payload.get("queries") or []
            if isinstance(raw_queries, str):
                raw_queries = [raw_queries]
            optimized_queries = []
            for item in raw_queries:
                optimized = str(item or "").strip()
                if optimized and len(optimized) <= 20 and not any(ch in optimized for ch in ["，", "。", "\n", "？", "?"]):
                    if optimized not in optimized_queries:
                        optimized_queries.append(optimized)
                if len(optimized_queries) >= 3:
                    break
            reason = str(payload.get("reason") or "").strip()
            if optimized_queries:
                logger.log("thought_step", {"message": f"DeepSeek V4 已将“{original_query}”快速优化为 3 个菜谱检索词：{optimized_queries}。{reason}"})
                return optimized_queries
        except Exception as exc:
            logger.log("thought_step", {"message": f"大模型检索词优化失败，改用本地规则兜底：{type(exc).__name__}: {exc}"})
        return fallback_queries

    def _load_home_recipe_plan_cache(self) -> dict:
        try:
            if HOME_RECIPE_PLAN_CACHE_PATH.exists():
                data = json.loads(HOME_RECIPE_PLAN_CACHE_PATH.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
        return {}

    def _save_home_recipe_plan_cache(self, cache: dict) -> None:
        try:
            HOME_RECIPE_PLAN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            HOME_RECIPE_PLAN_CACHE_PATH.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return

    def _home_recipe_plan_cache_key(
        self,
        user_query: str,
        disease: str,
        dining_people_count: int,
        recent_names: list[str],
    ) -> str:
        raw = json.dumps({
            "version": "compact_menu_v3",
            "query": user_query or "",
            "disease": disease or "",
            "people": dining_people_count,
            "recent": sorted(recent_names or [])[:20],
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _fallback_home_recipe_plan(self, user_query: str, disease: str, dining_people_count: int, recent_names: list[str]) -> list[dict]:
        original_query = (user_query or "").strip()
        if not original_query or original_query in {"推荐今日特色菜", "随便推荐", "不知道吃什么"}:
            items = choose_auto_menu_queries(dining_people_count, disease, recent_names)
        else:
            rewritten = rewrite_recipe_search_query(original_query, dining_people_count, disease)
            if should_expand_recipe_search_queries(original_query):
                pool = choose_auto_menu_queries(dining_people_count, disease, recent_names)
                items = [{"slot": infer_menu_slot(rewritten, dining_people_count), "query": rewritten}, *pool]
            else:
                items = [{"slot": infer_menu_slot(rewritten, dining_people_count), "query": rewritten}]
        seen = set()
        planned = []
        for item in items:
            name = str(item.get("query") or "").strip()
            if not name or name in seen or is_invalid_recipe_candidate_name(name):
                continue
            seen.add(name)
            local_recipe = (_search_recipes_from_local_cache(name, 1, recent_names) or [{}])[0]
            planned.append({
                "name": name,
                "menu_slot": item.get("slot") or infer_menu_slot(name, dining_people_count),
                "reason": "本地规则生成的候选菜",
                "search_keywords": name,
                "avoid_reason": "",
                "description": local_recipe.get("description") or f"{name}的家常做法，适合当前场景参考。",
                "ingredients": local_recipe.get("ingredients") or [],
                "condiments": local_recipe.get("condiments") or [],
                "steps": local_recipe.get("steps") or [],
                "calories": local_recipe.get("calories") or 300,
            })
            if len(planned) >= 3:
                break
        return planned or [{
            "name": "番茄炒蛋",
            "menu_slot": "单人主菜" if dining_people_count <= 1 else "荤菜",
            "reason": "经典家常菜，适合快速兜底",
            "search_keywords": "番茄炒蛋 家常做法",
            "avoid_reason": "",
            "description": "酸甜下饭，做法快手的国民家常菜。",
            "ingredients": ["番茄 2个", "鸡蛋 3个", "小葱 1根"],
            "condiments": ["盐 2g", "白糖 5g", "食用油 20ml", "番茄酱 10g"],
            "steps": [
                "番茄洗净切块，鸡蛋加少量盐打散，小葱切葱花。",
                "热锅下油倒入蛋液，炒至刚凝固后盛出，保持嫩滑。",
                "锅中补少量油，下番茄炒出红汁，加入白糖和少量盐调味。",
                "倒回鸡蛋快速翻匀，汤汁裹住蛋块后撒葱花出锅。",
            ],
            "calories": 260,
        }]

    async def plan_home_recipe_candidates(
        self,
        user_query: str,
        disease: str,
        dining_people_count: int,
        recent_names: list[str],
        logger: AuditLogger,
    ) -> list[dict]:
        """Plan 3 dish candidates before source-specific recipe detail extraction."""
        original_query = (user_query or "").strip() or "不知道吃什么，推荐今天适合做的家常菜"
        fallback = self._fallback_home_recipe_plan(original_query, disease, dining_people_count, recent_names)
        cache_key = self._home_recipe_plan_cache_key(original_query, disease, dining_people_count, recent_names)
        cache_ttl = int(os.getenv("HOME_RECIPE_PLAN_CACHE_TTL_SECONDS", "86400") or "0")
        if cache_ttl > 0:
            item = self._load_home_recipe_plan_cache().get(cache_key)
            if isinstance(item, dict) and time.time() - float(item.get("updated_at_epoch") or 0) <= cache_ttl:
                cached_plan = item.get("plan")
                if isinstance(cached_plan, list) and cached_plan:
                    logger.log("thought_step", {"message": f"已命中 DeepSeek 菜单规划缓存：{[p.get('name') for p in cached_plan[:3]]}"})
                    return cached_plan[:3]

        if self.is_offline:
            return fallback

        prompt = f"""
你是一个家庭做饭菜单规划 Agent。请根据用户需求、人数、健康状态和近期足迹，快速规划 3 个具体候选菜，并直接给出可执行的家常菜谱结构。后续 Tavily 只负责补图片，不负责生成菜谱内容。

硬性要求：
1. 每个候选必须包含具体食材、具体调料和具体做法步骤。
2. 候选必须是具体菜名，不要输出“辣一点的”“下饭菜”“随便”这类泛词。
3. 尽量避开近期吃过的菜；如果无法完全避开，要写入 avoid_reason。
4. 单人默认给 3 个单人主菜候选；多人优先覆盖荤菜、素菜、汤等不同槽位。
5. 食材至少 3 项，调料至少 3 项，步骤至少 4 步；不要写“适量”“参考原文”“看网页”这类占位内容。
6. 不要深度思考，不要输出推理过程，只返回 JSON。

用户原始需求：{original_query}
就餐人数：{dining_people_count}
身体状态：{disease or "正常健康"}
近期吃过或已加入备选的菜：{recent_names or []}

返回格式：
{{
  "candidates": [
    {{
      "name":"具体菜名",
      "menu_slot":"单人主菜/荤菜/素菜/汤/指定菜品",
      "reason":"推荐理由",
      "search_keywords":"菜名 家常做法 成品图",
      "avoid_reason":"",
      "description":"30字以内风味说明",
      "ingredients":["食材 用量", "食材 用量", "食材 用量"],
      "condiments":["调料 用量", "调料 用量", "调料 用量"],
      "steps":["步骤1", "步骤2", "步骤3", "步骤4"],
      "calories":300
    }}
  ]
}}
""".strip()
        prompt = f"""
你是一个家庭做饭菜单规划 Agent。根据用户需求、人数、健康状态和近期足迹，快速规划 3 个具体候选菜。
只做菜单规划，不生成食材、调料或步骤。

硬性要求：
1. 只返回合法 JSON，不输出 Markdown 或思考过程。
2. 候选必须是具体菜名，不要输出“辣一点的”“下饭菜”“随便”等泛词。
3. 尽量避开近期吃过或已加入备选的菜；无法避开时写入 avoid_reason。
4. 单人默认给 3 个单人主菜；多人优先覆盖荤菜、素菜、汤等不同槽位。

用户原始需求：{original_query}
就餐人数：{dining_people_count}
身体状态：{disease or "正常健康"}
近期吃过或已加入备选的菜：{recent_names or []}

返回格式：
{{
  "candidates": [
    {{
      "name": "具体菜名",
      "menu_slot": "单人主菜/荤菜/素菜/汤/指定菜品",
      "reason": "一句话推荐理由",
      "search_keywords": "菜名 家常做法 成品图",
      "avoid_reason": ""
    }}
  ]
}}
""".strip()
        try:
            plan_started = time.perf_counter()
            logger.log("menu_plan.start", {
                "model": self.model,
                "max_tokens": int(os.getenv("HOME_RECIPE_PLAN_MAX_TOKENS", "500") or "500"),
            })
            reply = await self.call_llm([
                {"role": "system", "content": "你只输出合法 JSON，不输出 Markdown，不输出思考过程。"},
                {"role": "user", "content": prompt},
            ], temperature=0.2, timeout_seconds=float(os.getenv("LLM_FAST_TIMEOUT_SECONDS", "25")), max_tokens=int(os.getenv("HOME_RECIPE_PLAN_MAX_TOKENS", "500") or "500"), disable_thinking=True)
            match = re.search(r"\{.*\}", reply, flags=re.DOTALL)
            payload = json.loads(match.group(0) if match else reply)
            raw_candidates = payload.get("candidates") or []
            planned = []
            seen = set()
            for raw in raw_candidates:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name") or "").strip()
                if (
                    not name
                    or name in seen
                    or is_invalid_recipe_candidate_name(name)
                    or any(ch in name for ch in ["，", "。", "\n", "？", "?"])
                ):
                    continue
                seen.add(name)
                ingredients = raw.get("ingredients") if isinstance(raw.get("ingredients"), list) else []
                condiments = raw.get("condiments") if isinstance(raw.get("condiments"), list) else []
                steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
                try:
                    calories = int(raw.get("calories") or 300)
                except (TypeError, ValueError):
                    calories = 300
                planned.append({
                    "name": name,
                    "menu_slot": str(raw.get("menu_slot") or infer_menu_slot(name, dining_people_count)).strip(),
                    "reason": str(raw.get("reason") or "").strip(),
                    "search_keywords": str(raw.get("search_keywords") or f"{name} 家常做法 成品图").strip(),
                    "avoid_reason": str(raw.get("avoid_reason") or "").strip(),
                    "description": str(raw.get("description") or "").strip(),
                    "ingredients": ingredients if len(ingredients) >= 3 else [],
                    "condiments": condiments if len(condiments) >= 3 else [],
                    "steps": steps if len(steps) >= 4 else [],
                    "calories": calories,
                })
                if len(planned) >= 3:
                    break
            if len(planned) < 3:
                for fallback_item in fallback:
                    fallback_name = str(fallback_item.get("name") or "").strip()
                    if (
                        fallback_name
                        and fallback_name not in seen
                        and not is_invalid_recipe_candidate_name(fallback_name)
                    ):
                        planned.append(fallback_item)
                        seen.add(fallback_name)
                    if len(planned) >= 3:
                        break
            if planned:
                if cache_ttl > 0:
                    cache = self._load_home_recipe_plan_cache()
                    cache[cache_key] = {
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "updated_at_epoch": time.time(),
                        "plan": planned,
                    }
                    self._save_home_recipe_plan_cache(cache)
                logger.log("menu_plan.done", {
                    "elapsed_ms": int((time.perf_counter() - plan_started) * 1000),
                    "candidate_names": [p["name"] for p in planned],
                })
                logger.log("thought_step", {"message": f"DeepSeek 已完成菜单规划：{[p['name'] for p in planned]}。"})
                return planned
        except Exception as exc:
            logger.log("menu_plan.done", {
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            })
            logger.log("thought_step", {"message": f"DeepSeek 菜单规划失败，改用本地规则兜底：{type(exc).__name__}: {exc}"})
        return fallback

    async def _generate_recipe_detail_with_llm(
        self,
        item: dict,
        dining_people_count: int,
        disease: str,
        logger: AuditLogger,
    ) -> dict | None:
        """Generate one structured recipe as the final fallback for a planned dish."""
        if self.is_offline:
            return None
        name = str(item.get("name") or item.get("query") or item.get("search_keywords") or "").strip()
        if not name:
            return None
        started = time.perf_counter()
        logger.log("recipe_detail.llm.start", {"name": name, "model": self.model})
        prompt = f"""
你是一个家常菜谱结构化助手。请只为一个菜生成可直接执行的家常做法。

硬性要求：
1. 只输出合法 JSON，不输出 Markdown 或思考过程。
2. 食材至少 3 项，调料至少 3 项，步骤至少 4 步。
3. 不要写“适量”“参考原文”“看网页”等占位内容。
4. 步骤要包含处理、下锅火候、调味、成熟判断或出锅。

菜名：{name}
菜单槽位：{item.get("slot") or item.get("menu_slot") or infer_menu_slot(name, dining_people_count)}
推荐理由：{item.get("reason") or ""}
就餐人数：{dining_people_count}
身体状态：{disease or "正常健康"}

返回格式：
{{
  "name": "{name}",
  "description": "30字以内风味说明",
  "ingredients": ["食材 用量", "食材 用量", "食材 用量"],
  "condiments": ["调料 用量", "调料 用量", "调料 用量"],
  "steps": ["步骤1", "步骤2", "步骤3", "步骤4"],
  "calories": 300
}}
""".strip()
        try:
            reply = await asyncio.wait_for(
                self.call_llm([
                    {"role": "system", "content": "你只输出合法 JSON，不输出 Markdown，不输出思考过程。"},
                    {"role": "user", "content": prompt},
                ], temperature=0.2, timeout_seconds=float(os.getenv("HOME_RECIPE_DETAIL_REQUEST_TIMEOUT_SECONDS", os.getenv("LLM_FAST_TIMEOUT_SECONDS", "25"))), max_tokens=int(os.getenv("HOME_RECIPE_DETAIL_MAX_TOKENS", "850") or "850"), disable_thinking=True),
                timeout=float(os.getenv("HOME_RECIPE_DETAIL_TIMEOUT_SECONDS", "30") or "30"),
            )
            match = re.search(r"\{.*\}", reply, flags=re.DOTALL)
            payload = json.loads(match.group(0) if match else reply)
            if not isinstance(payload, dict):
                raise ValueError("recipe detail payload is not object")
            ingredients = payload.get("ingredients") if isinstance(payload.get("ingredients"), list) else []
            condiments = payload.get("condiments") if isinstance(payload.get("condiments"), list) else []
            steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
            if len(ingredients) < 3 or len(condiments) < 3 or len(steps) < 4:
                raise ValueError("recipe detail missing concrete fields")
            try:
                calories = int(payload.get("calories") or 300)
            except (TypeError, ValueError):
                calories = 300
            logger.log("recipe_detail.llm.done", {
                "name": name,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "success": True,
            })
            return {
                "name": str(payload.get("name") or name).strip(),
                "description": str(payload.get("description") or item.get("reason") or f"{name}的家常做法。").strip(),
                "ingredients": [str(value).strip() for value in ingredients if str(value).strip()],
                "condiments": [str(value).strip() for value in condiments if str(value).strip()],
                "steps": [str(value).strip() for value in steps if str(value).strip()],
                "calories": calories,
            }
        except Exception as exc:
            logger.log("recipe_detail.llm.done", {
                "name": name,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            })
            return None

    async def get_recipe_recommendation(self, user_id: str, query: str, disease: str = "", dining_people_count: int = 1, session_id: str = "temp_session", selected_items: list[dict] | None = None) -> dict:
        """
        家庭主厨做菜决策链路（支持 3 个候选推荐与低优先级重排）
        """
        logger = AuditLogger(session_id)
        '''
        gaode_center = None
        if isinstance(business_area_context, dict):
            try:
                if business_area_context.get("lng") is not None and business_area_context.get("lat") is not None:
                    gaode_center = (float(business_area_context["lng"]), float(business_area_context["lat"]))
            except (TypeError, ValueError):
                gaode_center = None

        logger.log("thought_step", {"message": "正在通过高德地图搜索商圈内评分 4.0 以上餐厅候选..."})
        gaode_restaurants = await search_gaode_restaurants(
            location,
            query,
            budget,
            exclude_names=[],
            center=gaode_center,
            rec_count=3,
        )
        if gaode_restaurants:
            logger.log("thought_step", {"message": "正在为高德餐厅候选补充小红书推荐菜、种草描述和图片..."})
            dining_candidates = await enrich_restaurants_with_xhs_notes(
                gaode_restaurants,
                location,
                query,
                rec_count=3,
            )
            logger.log("thought_step", {"message": "正在按每家餐厅坐标搜索饭后可顺路去的影院、电玩城、台球、猫咖和展览..."})
            after_place_groups = await asyncio.gather(*[
                search_gaode_entertainment(
                    rest.get("address") or location,
                    query,
                    center=restaurant_center_from_gaode(rest) or gaode_center,
                    rec_count=3,
                )
                for rest in dining_candidates[:3]
            ])

            recommendations = []
            for idx, rest in enumerate(dining_candidates[:3]):
                after_places = after_place_groups[idx] if idx < len(after_place_groups) else []
                after_lines = [
                    f" - {place.get('name', '饭后去处')}：{place.get('description', '')}"
                    for place in after_places[:3]
                ]
                after_text = "\n".join(after_lines) if after_lines else " - 高德地图暂未搜索到稳定的饭后去处，可换一个商圈或扩大半径。"
                store_name = rest.get("store_name") or rest.get("name") or "餐厅候选"
                recommended_dishes = rest.get("recommended_dishes") or query or "到店优先看招牌菜/高赞菜"
                address = rest.get("address") or location
                xhs_seed_text = rest.get("xhs_seed_text") or "没有搜索到相关小红书种草内容"
                rec_text = (
                    f"【商圈探店决策】为您筛到餐厅候选：{store_name}\n"
                    f" - 店名：{store_name}\n"
                    f" - 推荐菜品：{recommended_dishes}\n"
                    f" - 具体地址：{address}\n"
                    f" - 小红书种草：{xhs_seed_text}\n\n"
                    f"【饭后可以顺路做什么】\n{after_text}"
                )
                recommendations.append({
                    "name": rest.get("name") or store_name,
                    "store_name": store_name,
                    "recommended_dishes": recommended_dishes,
                    "type": "restaurant",
                    "image_url": proxied_image_url(rest.get("image_url", "")),
                    "source_image_urls": [proxied_image_url(url) for url in (rest.get("source_image_urls") or [])],
                    "description": rest.get("description", ""),
                    "business_area": location,
                    "taste_query": query or "不限",
                    "budget": budget,
                    "rating": rest.get("rating"),
                    "avg_cost": rest.get("avg_cost"),
                    "address": address,
                    "distance": rest.get("distance", ""),
                    "liked_count": rest.get("liked_count", ""),
                    "collected_count": rest.get("collected_count", ""),
                    "comment_count": rest.get("comment_count", ""),
                    "author": rest.get("author", ""),
                    "xhs_note_status": rest.get("xhs_note_status", "none"),
                    "xhs_note_title": rest.get("xhs_note_title", ""),
                    "xhs_seed_text": xhs_seed_text,
                    "xhs_source_url": rest.get("xhs_source_url", ""),
                    "calories": int(budget),
                    "is_eaten": False,
                    "recommendation_text": rec_text,
                    "navigation_info": None,
                    "graph_rag_path": [],
                    "health_explanation": "安全：餐厅候选来自高德地图，小红书仅作为种草参考；具体点单仍需按身体状态自行判断。",
                    "source": rest.get("source", "高德地图餐厅"),
                    "source_url": rest.get("source_url", ""),
                    "after_meal_places": after_places,
                    "debug_timeline": rest.get("debug_timeline", []),
                })

            main_rec = recommendations[0]
            return {
                "user_id": user_id,
                "mode": "dining_out",
                "recommendations": recommendations,
                "recommendation_text": main_rec["recommendation_text"],
                "graph_rag_path": [],
                "health_explanation": main_rec["health_explanation"],
                "navigation_info": None,
                "source": main_rec.get("source", "高德地图餐厅"),
                "image_url": main_rec.get("image_url", ""),
                "search_timeline": main_rec.get("debug_timeline", []),
            }
        logger.log("thought_step", {"message": "正在初始化家庭主厨做菜推荐链路..."})

        '''
        original_query = (query or "").strip()
        is_auto_recipe_query = not original_query or original_query in {"推荐今日特色菜", "随便推荐", "不知道吃什么"}
        is_direct_slot_refill_query = is_planned_slot_query(original_query, dining_people_count)
        is_slot_refill_query = is_direct_slot_refill_query
        query = original_query or "推荐今日特色菜"

        # 1. 意图穿透判定
        force_include = False
        if "再吃" in query or "还要吃" in query or "还想吃" in query or "就是想吃" in query:
            force_include = True
            
        if self.is_offline:
            logger.log("social_fetch_failed", {"platform": "小红书/下厨房 API", "error": "LLM_API_KEY 缺失，自动降级为预置缓存"})

        # 2. Graph-RAG 安全性前置校验
        blocked_food = []
        health_explanation = "安全：暂未检测到食物相克或过敏冲突。"
        graph_path = []
        is_blocked_by_health = False
        
        logger.log("thought_step", {"message": "正在通过 Graph-RAG 碰撞多轮禁忌食材图谱..."})
        if disease:
            foods_to_check = ["牛肉", "鸭肉", "啤酒", "海鲜"]
            for food in foods_to_check:
                if food in query or food == query:
                    print("\n" + "="*80, flush=True)
                    print(f"【Tool Call】GraphRAG 决策调用 query_diet_safety...", flush=True)
                    print(f"  参数: {{'disease': '{disease}', 'food': '{food}'}}", flush=True)
                    print("="*80 + "\n", flush=True)
                    
                    logger.log("tool_call", {"tool": "query_diet_safety", "args": {"disease": disease, "food": food}})
                    safety_res = self.retriever.query_diet_safety(disease, food)
                    if not safety_res["safety"]:
                        blocked_food.append(food)
                        health_explanation = safety_res["reason"]
                        graph_path = safety_res["path"]
                        is_blocked_by_health = True
        
        if blocked_food:
            logger.log("thought_step", {"message": f"Graph-RAG 校验结束：发现与您的身体状况（{disease}）冲突的食材：{blocked_food}"})
        else:
            logger.log("thought_step", {"message": "Graph-RAG 校验结束：未检测到就餐冲突，安全前置校验通过。"})

        # 3. Milvus 近期吃过菜品检索（基于向量相似度语义去重）
        logger.log("thought_step", {"message": "正在检索 Milvus 近期吃货足迹并生成语义向量..."})
        print("\n" + "="*80, flush=True)
        print(f"【Milvus 向量检索】对查询词 '{query}' 生成向量并碰撞近期足迹...", flush=True)
        query_emb = await self.get_embedding(query)
        similar_items = self.db.search_similar_footprints(user_id, query_emb, limit=5, cutoff_days=7)
        
        # 提取 L2 距离小于 0.25 (极度相似) 或者是 mock 完全相似的就餐足迹名字
        similar_names = [hit["item_name"] for hit in similar_items if hit["distance"] <= 0.25]
        
        # 结合原有的普通字面检索，形成双重去重安全网
        recent_footprints = self.db.get_recent_footprints(user_id, days=7)
        selected_items = selected_items or []
        selected_names = [
            str(item.get("name") or item.get("store_name") or "").strip()
            for item in selected_items
            if isinstance(item, dict) and str(item.get("name") or item.get("store_name") or "").strip()
        ]
        all_eaten_names = list(set(similar_names + recent_footprints + selected_names))
        
        print(f"【Milvus 向量去重匹配】相似去重: {similar_names} | 传统历史去重: {recent_footprints}", flush=True)
        print("="*80 + "\n", flush=True)
        logger.log("thought_step", {"message": f"Milvus 语义碰撞完成。相似去重足迹：{similar_names}，传统历史足迹：{recent_footprints}，当前已选菜品：{selected_names}"})

        home_recipe_source_mode = os.getenv("HOME_RECIPE_SOURCE_MODE", "tavily_only").strip() or "tavily_only"
        xhs_probe_enabled = home_recipe_source_mode != "tavily_only" and os.getenv("AIONE_ENABLED", "0") == "1"
        xhs_probe_query = rewrite_recipe_search_query(original_query, dining_people_count, disease).strip() if (original_query or "").strip() else "家常菜"
        menu_plan_task = asyncio.create_task(self.plan_home_recipe_candidates(
            original_query,
            disease,
            dining_people_count,
            all_eaten_names,
            logger,
        ))
        xhs_probe_task = None
        if xhs_probe_enabled:
            xhs_probe_task = asyncio.create_task(probe_xhs_available(xhs_probe_query, limit=3))
        planned_candidates = await menu_plan_task
        xhs_probe = {"available": False, "candidate_count": 0, "reason": "disabled", "debug_timeline": []}
        if xhs_probe_task:
            try:
                xhs_probe = await xhs_probe_task
            except Exception as exc:
                xhs_probe = {"available": False, "candidate_count": 0, "reason": type(exc).__name__, "debug_timeline": []}
                logger.log("xhs_probe.done", {"available": False, "error": str(exc)[:300]})
        xhs_probe_timeline = xhs_probe.get("debug_timeline") or []
        logger.log("xhs_probe.done", {
            "available": bool(xhs_probe.get("available")),
            "candidate_count": xhs_probe.get("candidate_count", 0),
            "reason": xhs_probe.get("reason", ""),
            "debug_timeline": xhs_probe_timeline,
        })
        xhs_probe_last_event = xhs_probe_timeline[-1] if xhs_probe_timeline else {}
        safe_print(
            "[XHS_PROBE_DIAG] "
            f"available={bool(xhs_probe.get('available'))} "
            f"candidate_count={xhs_probe.get('candidate_count', 0)} "
            f"reason={xhs_probe.get('reason', '')} "
            f"last_event={xhs_probe_last_event}",
            flush=True,
        )
        explicit_rewritten_query = ""
        if (original_query or "").strip():
            explicit_rewritten_query = rewrite_recipe_search_query(original_query, dining_people_count, disease).strip()
        if explicit_rewritten_query and not is_invalid_recipe_candidate_name(explicit_rewritten_query):
            force_include = True
            def _planned_name(item: dict) -> str:
                return str(item.get("name") or item.get("search_keywords") or item.get("query") or "").strip()

            matched_items = []
            remaining_items = []
            for candidate in planned_candidates:
                candidate_name = _planned_name(candidate)
                if candidate_name and (
                    candidate_name == explicit_rewritten_query
                    or candidate_name in explicit_rewritten_query
                    or explicit_rewritten_query in candidate_name
                ):
                    matched_items.append(candidate)
                else:
                    remaining_items.append(candidate)
            if matched_items:
                planned_candidates = matched_items + remaining_items
            elif not any(_planned_name(item) == explicit_rewritten_query for item in planned_candidates):
                local_recipe = (_search_recipes_from_local_cache(explicit_rewritten_query, 1, selected_names) or [{}])[0]
                planned_candidates = [
                    {
                        "name": explicit_rewritten_query,
                        "menu_slot": infer_menu_slot(explicit_rewritten_query, dining_people_count),
                        "reason": "用户明确输入后优先锁定的候选菜",
                        "search_keywords": explicit_rewritten_query,
                        "avoid_reason": "",
                        "description": local_recipe.get("description") or f"{explicit_rewritten_query}的家常做法。",
                        "ingredients": local_recipe.get("ingredients") or [],
                        "condiments": local_recipe.get("condiments") or [],
                        "steps": local_recipe.get("steps") or [],
                        "calories": local_recipe.get("calories") or 300,
                    },
                    *planned_candidates,
                ]
            planned_candidates = planned_candidates[:3]
            logger.log("thought_step", {"message": f"已将用户明确输入改写后的菜名置顶：{explicit_rewritten_query}"})
        if is_slot_refill_query:
            planned_candidates = [
                item for item in planned_candidates
                if item.get("menu_slot") == infer_menu_slot(original_query, dining_people_count)
            ] or planned_candidates[:1]
        auto_menu_queries = [
            {
                "slot": item.get("menu_slot") or infer_menu_slot(item.get("name", ""), dining_people_count),
                "query": item.get("name") or item.get("search_keywords") or query,
                "search_keywords": item.get("search_keywords") or item.get("name") or query,
                "name": item.get("name") or item.get("search_keywords") or query,
                "reason": item.get("reason", ""),
                "avoid_reason": item.get("avoid_reason", ""),
                "description": item.get("description", ""),
                "ingredients": item.get("ingredients") or [],
                "condiments": item.get("condiments") or [],
                "steps": item.get("steps") or [],
                "calories": item.get("calories") or 300,
            }
            for item in planned_candidates[:3]
        ]
        query = auto_menu_queries[0]["name"] if auto_menu_queries else query
        logger.log(
            "thought_step",
            {"message": f"已根据提示词、人数、健康状态和近期足迹规划 3 个候选菜：{auto_menu_queries}。"}
        )

        # 4. 补全候选食谱详情：
        # DeepSeek 只负责先规划 3 个候选；小红书可用时按候选菜名获取做法和图片并结构化。
        # 只有小红书不可用或没有可采纳详情时，才保留 DeepSeek 的结构化菜谱，并用 Tavily 只补成品图。
        xhs_first = bool(xhs_probe.get("available"))
        xhs_detail_candidate_limit = max(0, int(os.getenv("AIONE_XHS_HOME_DETAIL_CANDIDATE_LIMIT", "3") or "3"))
        xhs_detail_timeout = max(3.0, float(os.getenv("AIONE_XHS_HOME_DETAIL_TIMEOUT_SECONDS", "25") or "25"))
        logger.log("thought_step", {
            "message": (
                f"正在为 {len(auto_menu_queries)} 个候选菜补全图文详情："
                f"{'优先小红书详情抽取' if xhs_first else 'Tavily 图片容错模式'}。"
            )
        })
        candidates = []
        has_explicit_user_query = bool((original_query or "").strip())
        initial_excludes = selected_names if has_explicit_user_query else all_eaten_names
        rolling_excludes = [name for name in initial_excludes if name]
        desired_items = auto_menu_queries[:1] if is_slot_refill_query else auto_menu_queries[:3]

        async def _xhs_recipe_for_candidate(item: dict, excludes: list[str]) -> dict | None:
            if not xhs_first:
                return None
            # XHS works better with the concrete dish name. Long web-search
            # keywords such as "家常做法 成品图" often slow down or miss notes.
            candidate_query = item.get("name") or item.get("query") or item.get("search_keywords") or ""
            candidate_query = str(candidate_query or "").strip()
            query_variants = []
            if candidate_query:
                query_variants.append(f"{candidate_query}做法")
                query_variants.append(candidate_query)
            search_keywords = str(item.get("search_keywords") or "").strip()
            if search_keywords and search_keywords not in query_variants and len(search_keywords) <= 18:
                query_variants.append(search_keywords)

            for xhs_query in query_variants:
                detail_started = time.perf_counter()
                logger.log("recipe_detail.xhs.start", {"name": candidate_query, "query": xhs_query})
                try:
                    results = await asyncio.wait_for(
                        search_xhs_recipes_only(xhs_query, rec_count=1, exclude_names=excludes),
                        timeout=xhs_detail_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.log("recipe_detail.xhs.done", {
                        "name": candidate_query,
                        "query": xhs_query,
                        "elapsed_ms": int((time.perf_counter() - detail_started) * 1000),
                        "success": False,
                        "error_type": "TimeoutError",
                    })
                    logger.log("social_fetch_failed", {
                        "platform": "xhs_recipe_detail_timeout",
                        "query": xhs_query,
                        "timeout_seconds": xhs_detail_timeout,
                    })
                    continue
                except Exception as exc:
                    logger.log("recipe_detail.xhs.done", {
                        "name": candidate_query,
                        "query": xhs_query,
                        "elapsed_ms": int((time.perf_counter() - detail_started) * 1000),
                        "success": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    })
                    logger.log("social_fetch_failed", {
                        "platform": "xhs_recipe_detail",
                        "query": xhs_query,
                        "error": str(exc)[:500],
                    })
                    continue
                for result in results or []:
                    source_url = str(result.get("source_url") or "")
                    image_url = str(result.get("image_url") or "")
                    image_urls = result.get("source_image_urls") or []
                    timeline = result.get("debug_timeline") or []
                    has_xhs_event = any(
                        str(event.get("event") or "").startswith("social.xhs")
                        for event in timeline
                        if isinstance(event, dict)
                    )
                    if "xiaohongshu.com" in source_url or "xhscdn.com" in image_url or image_urls or has_xhs_event:
                        logger.log("recipe_detail.xhs.done", {
                            "name": candidate_query,
                            "query": xhs_query,
                            "elapsed_ms": int((time.perf_counter() - detail_started) * 1000),
                            "success": True,
                            "result_count": len(results or []),
                        })
                        return result
                logger.log("recipe_detail.xhs.done", {
                    "name": candidate_query,
                    "query": xhs_query,
                    "elapsed_ms": int((time.perf_counter() - detail_started) * 1000),
                    "success": False,
                    "result_count": len(results or []),
                })
            return None

        async def _image_info_for_candidate(item: dict, cand_name: str) -> dict:
            started = time.perf_counter()
            logger.log("tavily_image.start", {"name": cand_name, "query": item.get("search_keywords") or cand_name})
            try:
                image_info = await search_recipe_images_online(item.get("search_keywords") or cand_name, limit=4)
                logger.log("tavily_image.done", {
                    "name": cand_name,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "image_count": len(image_info.get("image_urls") or []),
                })
                return image_info
            except Exception as exc:
                logger.log("tavily_image.done", {
                    "name": cand_name,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "success": False,
                    "error": str(exc)[:300],
                })
                logger.log("social_fetch_failed", {"platform": "tavily_image_only", "query": cand_name, "error": str(exc)})
                return {"image_urls": [], "source_url": "", "debug_timeline": []}

        async def _complete_recipe_candidate(item_index: int, item: dict, excludes: list[str]) -> dict | None:
            cand_name = str(item.get("name") or item.get("query") or "").strip()
            if not cand_name:
                return None
            if any(excluded and (excluded in cand_name or cand_name in excluded) for excluded in excludes):
                logger.log("thought_step", {"message": f"跳过与已选/已吃菜品重复的候选：{cand_name}"})
                return None
            local_recipe = (_search_recipes_from_local_cache(cand_name, 1, excludes) or [{}])[0]

            image_task = asyncio.create_task(_image_info_for_candidate(item, cand_name))
            llm_task = asyncio.create_task(self._generate_recipe_detail_with_llm(item, dining_people_count, disease, logger))
            xhs_task = None
            if xhs_first and item_index < xhs_detail_candidate_limit:
                xhs_task = asyncio.create_task(_xhs_recipe_for_candidate(item, excludes))

            xhs_recipe = None
            if xhs_task:
                xhs_soft_wait = max(1.0, float(os.getenv("HOME_RECIPE_XHS_WAIT_SECONDS", "10") or "10"))
                try:
                    xhs_recipe = await asyncio.wait_for(xhs_task, timeout=xhs_soft_wait)
                except asyncio.TimeoutError:
                    xhs_task.cancel()
                    logger.log("recipe_detail.xhs.done", {
                        "name": cand_name,
                        "success": False,
                        "error_type": "SoftTimeout",
                        "timeout_seconds": xhs_soft_wait,
                    })
                except Exception as exc:
                    logger.log("recipe_detail.xhs.done", {
                        "name": cand_name,
                        "success": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    })
            llm_recipe = await llm_task
            image_info = await image_task
            image_urls = image_info.get("image_urls") or []

            if xhs_recipe:
                recipe = xhs_recipe.copy()
                recipe.setdefault("name", cand_name)
                recipe.setdefault("category", item.get("slot") or "小红书图文菜谱")
                recipe["source"] = recipe.get("source") or "小红书详情抽取"
                recipe["menu_slot"] = item["slot"]
                recipe["menu_query"] = item["query"]
                recipe["planned_name"] = cand_name
                recipe["planning_reason"] = item.get("reason", "")
                existing_images = recipe.get("source_image_urls") or []
                for image_url in image_urls:
                    if image_url not in existing_images:
                        existing_images.append(image_url)
                recipe["source_image_urls"] = existing_images
                if not recipe.get("image_url"):
                    recipe["image_url"] = existing_images[0] if existing_images else (local_recipe.get("image_url") or "/images/steamed_duck.png")
                if not recipe.get("source_url"):
                    recipe["source_url"] = image_info.get("source_url") or ""
                recipe["debug_timeline"] = [
                    *(recipe.get("debug_timeline") or []),
                    *(image_info.get("debug_timeline") or []),
                ]
                return recipe

            detail_source = llm_recipe or local_recipe or {}
            source_label = "Qwen 菜谱详情 + Tavily 成品图" if llm_recipe else f"{local_recipe.get('source') or '本地模板'}（LLM 详情不可用时兜底，图片来自 Tavily）"
            return {
                "name": cand_name,
                "category": item.get("slot") or "DeepSeek 菜单规划",
                "source": source_label,
                "source_url": image_info.get("source_url") or "",
                "image_url": image_urls[0] if image_urls else (local_recipe.get("image_url") or "/images/steamed_duck.png"),
                "source_image_urls": image_urls,
                "description": detail_source.get("description") or item.get("description") or item.get("reason") or f"{cand_name}的家常做法。",
                "ingredients": detail_source.get("ingredients") or item.get("ingredients") or [],
                "condiments": detail_source.get("condiments") or item.get("condiments") or [],
                "steps": detail_source.get("steps") or item.get("steps") or [],
                "calories": detail_source.get("calories") or item.get("calories") or 300,
                "menu_slot": item["slot"],
                "menu_query": item["query"],
                "planned_name": cand_name,
                "planning_reason": item.get("reason", ""),
                "debug_timeline": image_info.get("debug_timeline") or [],
            }

        completed_candidates = await asyncio.gather(*[
            _complete_recipe_candidate(item_index, item, rolling_excludes)
            for item_index, item in enumerate(desired_items)
        ])
        candidates = [candidate for candidate in completed_candidates if candidate]
        if not is_slot_refill_query and len(candidates) < 3:
            exclude_names = all_eaten_names + [str(cand.get("name") or "") for cand in candidates]
            for fallback in _search_recipes_from_local_cache(query, 3 - len(candidates), exclude_names):
                fallback = fallback.copy()
                fallback_name = str(fallback.get("name") or query)
                if any(fallback_name and (fallback_name == str(cand.get("name") or "")) for cand in candidates):
                    continue
                try:
                    image_info = await search_recipe_images_online(f"{fallback_name} 家常做法 成品图", limit=4)
                except Exception as exc:
                    image_info = {"image_urls": [], "source_url": "", "debug_timeline": []}
                    logger.log("social_fetch_failed", {"platform": "tavily_image_only_fallback", "query": fallback_name, "error": str(exc)})
                image_urls = image_info.get("image_urls") or []
                if image_urls:
                    fallback["image_url"] = image_urls[0]
                    fallback["source_image_urls"] = image_urls
                    fallback["source_url"] = image_info.get("source_url") or fallback.get("source_url") or ""
                fallback["source"] = f"{fallback.get('source') or '本地模板'}（DeepSeek/Tavily 候选不足时补齐，图片来自 Tavily）"
                fallback["debug_timeline"] = image_info.get("debug_timeline") or fallback.get("debug_timeline") or []
                fallback["menu_slot"] = infer_menu_slot(fallback.get("name", ""), dining_people_count)
                fallback["menu_query"] = fallback.get("name", query)
                candidates.append(fallback)
                if len(candidates) >= 3:
                    break
        if is_blocked_by_health:
            candidates = []
        processed_candidates = []
        
        for cand in candidates:
            name = cand["name"]
            clean_name = name.replace("经典", "").replace("秘制", "").strip()
            
            # 健康图谱强拦截（直接过滤）
            if any(bf in name for bf in blocked_food) or any(bf in clean_name for bf in blocked_food):
                continue
                
            # 计算近期是否吃过
            is_eaten = any(eaten in name or name in eaten or eaten in clean_name or clean_name in eaten for eaten in all_eaten_names)
            
            cand_copy = cand.copy()
            cand_copy["is_eaten"] = is_eaten
            processed_candidates.append(cand_copy)

        # 5. 如果因为健康安全拦截导致所有 candidates 为空，安全置换备选方案
        if not processed_candidates and is_blocked_by_health:
            logger.log("thought_step", {"message": "触发健康禁忌拦截！正在为您安全置换推荐凉性的【清蒸鸭肉】..."})
            print("\n" + "="*80, flush=True)
            print("【去重拦截】由于健康禁忌拦截，进入安全备选方案检索：【清蒸鸭肉】", flush=True)
            print("="*80 + "\n", flush=True)
            safe_candidates = await search_recipes_online("清蒸鸭肉")
            for cand in safe_candidates:
                cand_copy = cand.copy()
                cand_copy["is_eaten"] = False
                processed_candidates.append(cand_copy)
            health_explanation = f"安全警示：您想吃的食材与您的身体状况（{disease}）发生冲突被拦截。为了您的健康，已为您安全置换推荐凉性的【清蒸鸭肉】。{health_explanation}"

        # 6. 低优先级重排：吃过的菜移到末尾（无强吃意图时）
        logger.log("thought_step", {"message": f"正在根据就餐人数（{dining_people_count}人）进行工序优化与多人协作套膳重排..."})
        if not force_include and processed_candidates:
            not_eaten_list = [c for c in processed_candidates if not c["is_eaten"]]
            eaten_list = [c for c in processed_candidates if c["is_eaten"]]
            final_list = not_eaten_list + eaten_list
        else:
            final_list = processed_candidates

        # 7. 为每个候选独立生成推荐文本并打包
        recommendations = []
        for recipe in final_list[:3]:
            recipe_name = recipe["name"]
            recipe_calories = recipe["calories"]
            recipe_description = recipe["description"]
            menu_slot = recipe.get("menu_slot", "指定菜品")
            detail_ingredients = []
            detail_condiments = []
            detail_steps = []
            
            if dining_people_count == 1 or menu_slot in {"荤菜", "素菜", "汤"}:
                # 单人食减量；多人自动菜单按槽位展示单道菜教程，避免把荤素汤硬塞进同一详情。
                scaled_ingredients = []
                scale_factor = 0.5 if dining_people_count == 1 else max(1.5, dining_people_count * 0.7)
                for ing in recipe["ingredients"]:
                    if dining_people_count == 1:
                        if "2个" in ing: ing = ing.replace("2个", "1个")
                        elif "3个" in ing: ing = ing.replace("3个", "2个")
                        elif "300g" in ing: ing = ing.replace("300g", "150g")
                    else:
                        if "2个" in ing: ing = ing.replace("2个", f"{int(2*scale_factor)}个")
                        elif "3个" in ing: ing = ing.replace("3个", f"{int(3*scale_factor)}个")
                        elif "300g" in ing: ing = ing.replace("300g", f"{int(300*scale_factor)}g")
                    scaled_ingredients.append(ing)
                detail_ingredients = scaled_ingredients
                detail_condiments = recipe["condiments"]
                detail_steps = recipe["steps"]
                    
                if dining_people_count == 1:
                    rec_text = f"【超级吃货智能主厨推荐】（单人食精细版）为您推荐：{recipe_name}。"
                else:
                    rec_text = f"【超级吃货智能主厨推荐】（{dining_people_count}人菜单 · {menu_slot}槽位）为您推荐：{recipe_name}。"
                if recipe.get("is_eaten") and not force_include:
                    rec_text += "（💡您近期已享用过此菜品，已帮您移至列表靠后位置，供您参考）\n"
                elif recipe.get("is_eaten") and force_include:
                    rec_text += "（💡您明确指定了这个方向，本次仍优先展示，供您参考）\n"
                else:
                    rec_text += "\n"
                rec_text += f"描述：{recipe_description} (来源平台：{recipe.get('source', '下厨房')})\n\n"
                amount_label = "已自动缩减为 1人份量" if dining_people_count == 1 else f"已自动折算为 {dining_people_count}人份量"
                rec_text += f"【食材清单（{amount_label}）】：\n" + "\n".join([f" - {i}" for i in scaled_ingredients]) + "\n"
                rec_text += "【调料配比】：\n" + "\n".join([f" - {c}" for c in recipe["condiments"]]) + "\n\n"
                rec_text += "【精细化步骤指引】：\n" + "\n".join([f"{idx+1}. {step}" for idx, step in enumerate(recipe["steps"])])
            else:
                # 多人餐，推荐一荤一素一汤组合套膳！
                side_dish = "香菇蚝油菜心"
                soup_dish = "酸辣豆腐蛋花汤"
                
                # 避免主菜和副菜重叠
                if "菜心" in recipe_name or "香菇" in recipe_name:
                    side_dish = "经典番茄炒蛋"
                    soup_dish = "清汤紫菜豆腐汤"
                    
                combo_name = f"{recipe_name} 多人套膳 ({dining_people_count}人份)"
                rec_text = f"【超级吃货智能主厨推荐】为您推荐：【{combo_name}】（一荤一素一汤多人协作餐）！"
                if recipe.get("is_eaten") and not force_include:
                    rec_text += "（💡您近期已享用过此套膳，已帮您移至列表靠后位置，供您参考）\n"
                elif recipe.get("is_eaten") and force_include:
                    rec_text += "（💡您明确指定了这个方向，本次仍优先展示，供您参考）\n"
                else:
                    rec_text += "\n"
                rec_text += f"本膳由主菜【{recipe_name}】、副菜【{side_dish}】以及汤品【{soup_dish}】智能搭配而成。 (来源平台：{recipe.get('source', '下厨房')})\n\n"
                
                scale_factor = max(1.5, dining_people_count * 0.7)
                scaled_ingredients = []
                for ing in recipe["ingredients"]:
                    if "2个" in ing: ing = ing.replace("2个", f"{int(2*scale_factor)}个")
                    elif "3个" in ing: ing = ing.replace("3个", f"{int(3*scale_factor)}个")
                    elif "300g" in ing: ing = ing.replace("300g", f"{int(300*scale_factor)}g")
                    scaled_ingredients.append(ing)
                
                rec_text += "🥗【食材采购补齐清单（多人分量合并折算）】：\n"
                rec_text += " - [主菜] " + ", ".join(scaled_ingredients) + "\n"
                rec_text += " - [副菜] " + ("鲜香菇 8个, 菜心 450g" if side_dish == "香菇蚝油菜心" else "西红柿 2个, 鸡蛋 4个") + "\n"
                rec_text += " - [汤品] " + ("西红柿 1个, 鸡蛋 1个, 豆腐 1块" if soup_dish == "酸辣豆腐蛋花汤" else "紫菜 10g, 豆腐 1块, 葱花 适量") + "\n\n"
                
                rec_text += "👩‍🍳【多工序并联烹饪次序与时间线（多智能体优化建议）】：\n"
                detail_ingredients = [
                    "[主菜] " + ", ".join(scaled_ingredients),
                    "[副菜] " + ("鲜香菇 8个, 菜心 450g" if side_dish == "香菇蚝油菜心" else "西红柿 2个, 鸡蛋 4个"),
                    "[汤品] " + ("西红柿 1个, 鸡蛋 1个, 豆腐 1块" if soup_dish == "酸辣豆腐蛋花汤" else "紫菜 10g, 豆腐 1块, 葱花 适量"),
                ]
                detail_condiments = recipe["condiments"]
                detail_steps = [
                    "【前置准备（第 1-5 分钟）】清洗全部食材，将副菜蔬菜洗净焯水，鸡蛋打散，西红柿切块。",
                    f"【副菜制作（第 6-12 分钟）】快速炒制副菜【{side_dish}】，盛出装盘。",
                    f"【主菜烹饪（第 13-20 分钟）】大火热锅，按精细步骤烹饪主菜【{recipe_name}】，注意掌握物态变化与火候控制。",
                    f"【汤品制作（第 21-25 分钟）】大火烧水制作【{soup_dish}】，加入紫菜/豆腐调味，撒小葱即可出锅。",
                    "【美味上桌】一荤一素一汤同时温热上桌，预计总工期 25 分钟。",
                ]
                rec_text += "\n".join([f" {idx+1}. {step}" for idx, step in enumerate(detail_steps)]) + "\n"
                
            recommendations.append({
                "name": recipe_name,
                "image_url": proxied_image_url(recipe.get("image_url", "")),
                "source_image_urls": [
                    proxied_image_url(url)
                    for url in (recipe.get("source_image_urls", []) or [])
                ],
                "raw_source_image_urls": recipe.get("source_image_urls", []),
                "description": recipe_description,
                "calories": recipe_calories,
                "ingredients": detail_ingredients,
                "condiments": detail_condiments,
                "steps": detail_steps,
                "menu_slot": menu_slot,
                "is_eaten": recipe.get("is_eaten", False),
                "recommendation_text": rec_text,
                "source": recipe.get("source", "下厨房"),
                "source_url": recipe.get("source_url", ""),
                "debug_timeline": recipe.get("debug_timeline", []),
            })

        # 为兼容性，保留最外层的 recommendation_text/image_url 作为默认主推荐内容
        main_rec = recommendations[0] if recommendations else {
            "name": "经典番茄炒蛋",
            "image_url": "/images/classic_tomato_egg.png",
            "description": "经典美味的番茄炒蛋",
            "calories": 250,
            "is_eaten": False,
            "recommendation_text": "【超级吃货智能主厨推荐】经典番茄炒蛋\n食材清单：番茄 2个，鸡蛋 3个",
            "source": "下厨房"
        }

        return {
            "user_id": user_id,
            "mode": "home_cooking",
            "recommendations": recommendations,
            "recommendation_text": main_rec["recommendation_text"],
            "graph_rag_path": graph_path,
            "health_explanation": health_explanation,
            "navigation_info": None,
            "source": main_rec.get("source", "下厨房"),
            "image_url": main_rec.get("image_url", ""),
            "search_timeline": main_rec.get("debug_timeline", []),
        }

    async def get_restaurant_recommendation(self, user_id: str, location: str, query: str, disease: str = "", budget: float = 150, dining_people_count: int = 1, session_id: str = "temp_session", business_area_context: dict | None = None) -> dict:
        """
        外出探店多智能体决策链路（支持多候选 recommendations 列表）
        """
        logger = AuditLogger(session_id)
        gaode_center = None
        if isinstance(business_area_context, dict):
            try:
                if business_area_context.get("lng") is not None and business_area_context.get("lat") is not None:
                    gaode_center = (float(business_area_context["lng"]), float(business_area_context["lat"]))
            except (TypeError, ValueError):
                gaode_center = None

        logger.log("thought_step", {"message": "正在通过高德地图搜索商圈内评分 4.0 以上餐厅候选..."})
        gaode_restaurants = await search_gaode_restaurants(
            location,
            query,
            budget,
            exclude_names=[],
            center=gaode_center,
            rec_count=3,
        )
        if gaode_restaurants:
            logger.log("thought_step", {"message": "正在为高德餐厅候选补充小红书推荐菜、种草描述和图片..."})
            dining_candidates = await enrich_restaurants_with_xhs_notes(
                gaode_restaurants,
                location,
                query,
                rec_count=3,
            )
            logger.log("thought_step", {"message": "正在按每家餐厅坐标搜索饭后可顺路去的影院、电玩城、台球、猫咖和展览..."})
            after_place_groups = await asyncio.gather(*[
                search_gaode_entertainment(
                    rest.get("address") or location,
                    query,
                    center=restaurant_center_from_gaode(rest) or gaode_center,
                    rec_count=3,
                )
                for rest in dining_candidates[:3]
            ])

            recommendations = []
            for idx, rest in enumerate(dining_candidates[:3]):
                after_places = after_place_groups[idx] if idx < len(after_place_groups) else []
                after_lines = [
                    f" - {place.get('name', '饭后去处')}：{place.get('description', '')}"
                    for place in after_places[:3]
                ]
                after_text = "\n".join(after_lines) if after_lines else " - 高德地图暂未搜索到稳定的饭后去处，可换一个商圈或扩大半径。"
                store_name = rest.get("store_name") or rest.get("name") or "餐厅候选"
                recommended_dishes = rest.get("recommended_dishes") or query or "到店优先看招牌菜/高赞菜"
                address = rest.get("address") or location
                xhs_seed_text = rest.get("xhs_seed_text") or "没有搜索到相关小红书种草内容"
                rec_text = (
                    f"【商圈探店决策】为您筛到餐厅候选：{store_name}\n"
                    f" - 店名：{store_name}\n"
                    f" - 推荐菜品：{recommended_dishes}\n"
                    f" - 具体地址：{address}\n"
                    f" - 小红书种草：{xhs_seed_text}\n\n"
                    f"【饭后可以顺路做什么】\n{after_text}"
                )
                recommendations.append({
                    "name": rest.get("name") or store_name,
                    "store_name": store_name,
                    "recommended_dishes": recommended_dishes,
                    "type": "restaurant",
                    "image_url": proxied_image_url(rest.get("image_url", "")),
                    "source_image_urls": [proxied_image_url(url) for url in (rest.get("source_image_urls") or [])],
                    "description": rest.get("description", ""),
                    "business_area": location,
                    "taste_query": query or "不限",
                    "budget": budget,
                    "rating": rest.get("rating"),
                    "avg_cost": rest.get("avg_cost"),
                    "address": address,
                    "distance": rest.get("distance", ""),
                    "liked_count": rest.get("liked_count", ""),
                    "collected_count": rest.get("collected_count", ""),
                    "comment_count": rest.get("comment_count", ""),
                    "author": rest.get("author", ""),
                    "xhs_note_status": rest.get("xhs_note_status", "none"),
                    "xhs_note_title": rest.get("xhs_note_title", ""),
                    "xhs_seed_text": xhs_seed_text,
                    "xhs_source_url": rest.get("xhs_source_url", ""),
                    "calories": int(budget),
                    "is_eaten": False,
                    "recommendation_text": rec_text,
                    "navigation_info": None,
                    "graph_rag_path": [],
                    "health_explanation": "安全：餐厅候选来自高德地图，小红书仅作为种草参考；具体点单仍需按身体状态自行判断。",
                    "source": rest.get("source", "高德地图餐厅"),
                    "source_url": rest.get("source_url", ""),
                    "after_meal_places": after_places,
                    "debug_timeline": rest.get("debug_timeline", []),
                })

            main_rec = recommendations[0]
            return {
                "user_id": user_id,
                "mode": "dining_out",
                "recommendations": recommendations,
                "recommendation_text": main_rec["recommendation_text"],
                "graph_rag_path": [],
                "health_explanation": main_rec["health_explanation"],
                "navigation_info": None,
                "source": main_rec.get("source", "高德地图餐厅"),
                "image_url": main_rec.get("image_url", ""),
                "search_timeline": main_rec.get("debug_timeline", []),
            }
        logger.log("thought_step", {"message": "正在初始化商圈探店美食决策链路..."})
        logger.log("thought_step", {"message": "正在通过小红书搜索商圈内餐厅探店笔记和饭后逛玩攻略..."})
        social_plan = await search_xhs_dining_plan(location, query, budget, rec_count=3)
        after_places = social_plan.get("after_places") or []
        social_restaurants = [
            item for item in (social_plan.get("restaurants") or [])
            if item.get("store_name") and item.get("store_name") != "待从笔记确认店名"
        ]
        gaode_center = None
        if isinstance(business_area_context, dict):
            try:
                if business_area_context.get("lng") is not None and business_area_context.get("lat") is not None:
                    gaode_center = (float(business_area_context["lng"]), float(business_area_context["lat"]))
            except (TypeError, ValueError):
                gaode_center = None
        social_restaurants = await enrich_restaurants_with_gaode(
            social_restaurants,
            location,
            budget,
            center=gaode_center,
        )
        social_restaurants = social_restaurants[:2]
        gaode_restaurants = await search_gaode_restaurants(
            location,
            query,
            budget,
            exclude_names=[item.get("name", "") for item in social_restaurants],
            center=gaode_center,
            rec_count=max(3, 3 - len(social_restaurants)),
        )
        gaode_after_places = await search_gaode_entertainment(
            location,
            query,
            center=gaode_center,
            rec_count=3,
        )
        if gaode_after_places:
            after_places = gaode_after_places
        dining_candidates = [*social_restaurants, *gaode_restaurants]
        if dining_candidates:
            recommendations = []
            for rest in dining_candidates[:3]:
                after_lines = []
                for place in after_places[:3]:
                    after_lines.append(
                        f" - {place['name']}：{place['description']}"
                    )
                after_text = "\n".join(after_lines) if after_lines else " - 暂未抓到稳定的饭后去处，可在同一商圈内补搜影院、电玩城、台球、猫咖或展览。"
                store_name = rest.get("store_name") or rest.get("name") or "餐厅候选"
                recommended_dishes = rest.get("recommended_dishes") or query or "到店优先看招牌菜/高赞菜"
                address = rest.get("address") or location
                rec_text = (
                    f"【商圈探店决策】为您筛到餐厅候选：{store_name}\n"
                    f" - 店名：{store_name}\n"
                    f" - 推荐菜品：{recommended_dishes}\n"
                    f" - 具体地址：{address}\n\n"
                    f"【饭后可以顺路做什么】\n{after_text}"
                )
                recommendations.append({
                    "name": rest.get("name") or store_name,
                    "store_name": store_name,
                    "recommended_dishes": recommended_dishes,
                    "type": "restaurant",
                    "image_url": proxied_image_url(rest.get("image_url", "")),
                    "source_image_urls": [proxied_image_url(url) for url in (rest.get("source_image_urls") or [])],
                    "description": rest["description"],
                    "business_area": location,
                    "taste_query": query or "不限",
                    "budget": budget,
                    "rating": rest.get("rating"),
                    "avg_cost": rest.get("avg_cost"),
                    "address": address,
                    "distance": rest.get("distance", ""),
                    "liked_count": rest.get("liked_count", ""),
                    "collected_count": rest.get("collected_count", ""),
                    "comment_count": rest.get("comment_count", ""),
                    "author": rest.get("author", ""),
                    "calories": int(budget),
                    "is_eaten": False,
                    "recommendation_text": rec_text,
                    "navigation_info": None,
                    "graph_rag_path": [],
                    "health_explanation": "安全：餐厅候选来自小红书探店笔记，具体点单仍需按身体状态自行判断。",
                    "source": rest.get("source", "小红书探店笔记"),
                    "source_url": rest.get("source_url", ""),
                    "after_meal_places": after_places,
                    "debug_timeline": rest.get("debug_timeline", []),
                })

            main_rec = recommendations[0]
            return {
                "user_id": user_id,
                "mode": "dining_out",
                "recommendations": recommendations,
                "recommendation_text": main_rec["recommendation_text"],
                "graph_rag_path": [],
                "health_explanation": main_rec["health_explanation"],
                "navigation_info": None,
                "source": main_rec.get("source", "小红书探店笔记"),
                "image_url": main_rec.get("image_url", ""),
                "search_timeline": main_rec.get("debug_timeline", []),
            }

        logger.log("social_fetch_failed", {"platform": "小红书探店", "error": "未抓到可用餐厅候选，降级为本地餐厅示例"})

        # 1. 检索餐厅并排序
        logger.log("thought_step", {"message": f"正在检索 '{location}' 周边的 '{query}' 餐厅及玩乐项目..."})
        restaurants = search_nearby_restaurants(location, query, budget)
        
        if dining_people_count == 1:
            restaurants.sort(key=lambda x: 0 if "点心" in x["cuisine"] else 1)
        else:
            restaurants.sort(key=lambda x: 0 if "火锅" in x["cuisine"] or "桌菜" in x["cuisine"] else 1)

        # 2. Graph-RAG 安全性前置校验与红色健康警示
        logger.log("thought_step", {"message": "正在提取各商户招牌菜，通过 Graph-RAG 进行健康图谱碰撞校验..."})
        processed_rests = []
        for rest in restaurants:
            rest_warning = ""
            rest_explanation = "安全：未检测到过敏及就餐冲突。"
            is_blocked = False
            rest_graph_path = []
            
            items_to_check = rest["signature_dishes"] + [rest["cuisine"]]
            for item in items_to_check:
                for key_word in ["啤酒", "海鲜", "牛肉", "鸭肉", "毛肚"]:
                    if key_word in item:
                        print("\n" + "="*80, flush=True)
                        print(f"【Tool Call】GraphRAG 决策调用 query_diet_safety...", flush=True)
                        print(f"  参数: {{'disease': '{disease}', 'food': '{key_word}'}}", flush=True)
                        print("="*80 + "\n", flush=True)
                        
                        logger.log("tool_call", {"tool": "query_diet_safety", "args": {"disease": disease, "food": key_word}})
                        safety_res = self.retriever.query_diet_safety(disease, key_word)
                        if not safety_res["safety"]:
                            rest_warning = f"⚠️ 警告：该店特色中含有与您【{disease}】状况冲突的成分【{item}】（关联[[{key_word}]]），请在点餐时注意避开！"
                            rest_explanation = f"红色健康警告：{safety_res['reason']}"
                            rest_graph_path = safety_res["path"]
                            is_blocked = True
                            break
                if is_blocked:
                    break
                    
            rest_copy = rest.copy()
            rest_copy["warning"] = rest_warning
            rest_copy["health_explanation"] = rest_explanation
            rest_copy["graph_path"] = rest_graph_path
            processed_rests.append(rest_copy)

        if not processed_rests:
            return {
                "user_id": user_id,
                "mode": "dining_out",
                "recommendations": [],
                "recommendation_text": f"抱歉，附近在人均 {budget} 元内的 {query} 餐厅均与您的就餐要求不符。建议您调整搜索偏好。",
                "graph_rag_path": [],
                "health_explanation": "安全：未检测到过敏及就餐冲突。",
                "navigation_info": None,
                "source": "大众点评",
                "image_url": ""
            }

        # 3. 组装候选商户推荐
        recommendations = []
        for rest in processed_rests[:3]:
            rec_text = ""
            if rest.get("warning"):
                rec_text += f"<span style='color:#EC7063; font-weight:bold;'>{rest['warning']}</span>\n\n"
                
            rec_text += f"【超级吃货探店一站式决策】\n为您选中：【{rest['name']}】\n"
            rec_text += f" - 特色菜系: {rest['cuisine']} | 平均消费: 人均 {rest['avg_price']} 元 (来源平台: {rest.get('source', '大众点评')})\n"
            rec_text += f" - 各平台评价: {', '.join(rest['platforms'])}\n"
            rec_text += f" - 招牌必吃推荐: {', '.join(rest['signature_dishes'])}\n"
            rec_text += f" - 大众情感差评避坑: {rest['reviews']['bad']}\n"
            
            if dining_people_count == 1:
                rec_text += f"👤【1人食专享点单指南】：该店提供招牌单人特色套餐，份量适中，点单推荐享用 【{rest['signature_dishes'][0]}】，单人就餐十分友好！\n\n"
            else:
                rec_text += f"👥【{dining_people_count}人聚会分享点单指南】：建议点 【{', '.join(rest['signature_dishes'][:3])}】 作为分享合菜，已自动适配 {dining_people_count} 人用餐规模！\n\n"
                
            rec_text += f"🎉【吃喝玩乐一条龙增值推荐】：\n"
            rec_text += f" - 饮品推荐: 步行 {rest['nearby_dessert']['distance']} 至 {rest['nearby_dessert']['name']} 享用 【{rest['nearby_dessert']['recommended']}】\n"
            rec_text += f" - 餐后娱乐: 前往 {rest['nearby_entertainment']['name']} ({rest['nearby_entertainment']['distance']}) 进行 【{', '.join(rest['nearby_entertainment']['activities'])}】\n"

            recommendations.append({
                "name": rest["name"],
                "image_url": rest.get("image_url", ""),
                "description": f"特系: {rest['cuisine']} | 消费: 人均 {rest['avg_price']} 元",
                "calories": int(rest["avg_price"]),
                "is_eaten": False,
                "recommendation_text": rec_text,
                "navigation_info": None,
                "graph_rag_path": rest.get("graph_path", []),
                "health_explanation": rest.get("health_explanation", "安全：未检测到过敏及就餐冲突。"),
                "source": rest.get("source", "大众点评")
            })

        main_rec = recommendations[0]
        return {
            "user_id": user_id,
            "mode": "dining_out",
            "recommendations": recommendations,
            "recommendation_text": main_rec["recommendation_text"],
            "graph_rag_path": main_rec["graph_rag_path"],
            "health_explanation": main_rec["health_explanation"],
            "navigation_info": None,
            "source": main_rec.get("source", "大众点评"),
            "image_url": main_rec.get("image_url", "")
        }
