import time

import pytest

from src.agent.graph import FoodieGraph


def _fake_recipe(name, source="pytest"):
    return {
        "name": name,
        "description": f"{name} 的图文摘要",
        "calories": 300,
        "ingredients": ["主料 300g", "配菜 2 个"],
        "condiments": ["盐 适量"],
        "steps": ["处理食材", "下锅烹饪", "装盘"],
        "source": source,
        "source_url": f"https://example.com/{name}",
        "image_url": "https://example.com/image.webp",
        "source_image_urls": ["https://example.com/image.webp"],
        "debug_timeline": [],
    }


@pytest.mark.asyncio
async def test_multi_people_empty_recipe_query_returns_menu_slots(monkeypatch):
    """多人餐且未指定菜名时，应稳定规划荤菜/素菜/汤三个槽位。"""
    async def fake_search(query, rec_count=3, exclude_names=None):
        assert rec_count == 1
        return [_fake_recipe(query)]

    monkeypatch.setattr("src.agent.chatbot.search_recipes_online", fake_search)
    graph = FoodieGraph()
    state = {
        "session_id": f"session-auto-menu-{int(time.time())}",
        "user_id": f"user-auto-menu-{int(time.time())}",
        "mode": "home_cooking",
        "user_input": "",
        "current_disease": "正常健康",
        "dining_people_count": 3,
        "location": "五道口",
    }

    result = await graph.run(state)
    slots = [rec.get("menu_slot") for rec in result["recommendations"]]
    names = [rec.get("name") for rec in result["recommendations"]]
    assert slots == ["荤菜", "素菜", "汤"]
    assert len(set(names)) == 3


@pytest.mark.asyncio
async def test_multi_people_slot_refill_returns_single_slot_recipe(monkeypatch):
    """补位搜索到汤槽位时，只返回一个汤候选。"""
    async def fake_search(query, rec_count=3, exclude_names=None):
        assert query == "家常汤"
        assert rec_count == 1
        return [_fake_recipe("番茄豆腐汤")]

    monkeypatch.setattr("src.agent.chatbot.search_recipes_online", fake_search)
    graph = FoodieGraph()
    state = {
        "session_id": f"session-slot-refill-{int(time.time())}",
        "user_id": f"user-slot-refill-{int(time.time())}",
        "mode": "home_cooking",
        "user_input": "家常汤",
        "current_disease": "正常健康",
        "dining_people_count": 2,
        "location": "五道口",
    }

    result = await graph.run(state)
    assert len(result["recommendations"]) == 1
    rec = result["recommendations"][0]
    assert rec["menu_slot"] == "汤"
    assert rec["name"] == "番茄豆腐汤"


@pytest.mark.asyncio
async def test_restaurant_health_warning_flow_returns_dynamic_candidate():
    """探店结果来自高德/社交融合，不应绑定某个固定店名。"""
    graph = FoodieGraph()
    state = {
        "session_id": f"session-gout-{int(time.time())}",
        "user_id": f"user-gout-{int(time.time())}",
        "mode": "dining_out",
        "user_input": "我想去外面吃川菜",
        "current_disease": "痛风",
        "dining_people_count": 3,
        "location": "五道口",
    }

    result = await graph.run(state)
    assert result["recommendation_text"].strip()
    assert result["recommendations"]
    first = result["recommendations"][0]
    assert first.get("name") or first.get("store_name")
    assert first.get("address") or first.get("description")
    assert result["health_explanation"]


@pytest.mark.asyncio
async def test_session_reset_safety():
    """新 session/user_id 应使用新的健康状态，不被上一次状态污染。"""
    graph = FoodieGraph()
    user_id = f"user-reset-{int(time.time())}"

    blocked_state = {
        "session_id": f"session-reset-1-{int(time.time())}",
        "user_id": user_id,
        "mode": "home_cooking",
        "user_input": "牛肉",
        "current_disease": "感冒",
        "dining_people_count": 1,
        "location": "五道口",
    }
    blocked = await graph.run(blocked_state)
    assert blocked["recommendation_text"].strip()

    normal_state = {
        "session_id": f"session-reset-2-{int(time.time())}",
        "user_id": f"{user_id}_new",
        "mode": "home_cooking",
        "user_input": "番茄炒蛋",
        "current_disease": "正常健康",
        "dining_people_count": 1,
        "location": "五道口",
    }
    normal = await graph.run(normal_state)
    assert normal["recommendation_text"].strip()
    assert normal["recommendations"]
