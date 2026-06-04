# tests/test_deduplication.py

import pytest
import time
from src.agent.graph import FoodieGraph

@pytest.mark.asyncio
async def test_foodie_recommendation_deduplication():
    """测试 7 天内吃过去重机制"""
    graph = FoodieGraph()
    user_id = "test_user_dedup"
    session_id = f"session-{int(time.time())}"
    
    # 1. 确保清空测试用户的 mock 历史 (直接操作底层的 mock 列表)
    if graph.bot.db.is_mock:
        graph.bot.db.mock_footprints = [
            fp for fp in graph.bot.db.mock_footprints if fp["user_id"] != user_id
        ]
        
    # 2. 插入一个 7 天内的就餐历史（例如最近刚吃过白切鸡）
    graph.bot.db.insert_footprint(user_id, "正宗广式白切鸡", "recipe")
    
    # 3. 发起普通推荐，查询粤菜
    state = {
        "session_id": session_id,
        "user_id": user_id,
        "user_input": "我想吃粤菜，有什么推荐的？",
        "current_disease": "",
        "location": "五道口"
    }
    
    result_state = await graph.run(state)
    rec_text = result_state["recommendation_text"]
    
    # 因为刚吃过白切鸡，去重机制启动，系统不应该推荐白切鸡，而是推荐其他粤菜或者平替
    assert "白切鸡" not in rec_text


@pytest.mark.asyncio
async def test_foodie_intent_penetration():
    """测试用户主动提起时的意图穿透机制"""
    graph = FoodieGraph()
    user_id = "test_user_penetrate"
    session_id = f"session-{int(time.time())}"
    
    if graph.bot.db.is_mock:
        graph.bot.db.mock_footprints = [
            fp for fp in graph.bot.db.mock_footprints if fp["user_id"] != user_id
        ]
        
    # 1. 插入 7 天内的就餐历史 (刚刚吃过白切鸡)
    graph.bot.db.insert_footprint(user_id, "正宗广式白切鸡", "recipe")
    
    # 2. 用户强烈要求：主动指出“今天还想吃白切鸡”
    state = {
        "session_id": session_id,
        "user_id": user_id,
        "user_input": "我今天还想吃白切鸡！",
        "current_disease": "",
        "location": "五道口"
    }
    
    result_state = await graph.run(state)
    rec_text = result_state["recommendation_text"]
    
    # 因为用户强烈要求，触发意图穿透，依然推荐白切鸡！
    assert "白切鸡" in rec_text
