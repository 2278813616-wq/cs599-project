# tests/test_restaurant_agent.py

import pytest
import os
import time
from pathlib import Path
from src.agent.graph import FoodieGraph

@pytest.mark.asyncio
async def test_restaurant_recommendation_flow():
    """测试探店找餐馆一站式规划流程"""
    graph = FoodieGraph()
    user_id = "test_restaurant_user"
    session_id = f"session-rest-{int(time.time())}"
    
    state = {
        "session_id": session_id,
        "user_id": user_id,
        "user_input": "我想去外面吃川菜火锅",
        "current_disease": "",
        "location": "清华大学"
    }
    
    # 模拟外部输入为探店模式
    state["user_input"] = "我想去外面吃川菜"
    
    result = await graph.run(state)
    rec_text = result["recommendation_text"]
    
    # 1. 验证餐馆名和特色
    assert "老川鲁火锅" in rec_text
    
    # 2. 验证吃喝玩乐一条龙是否合并输出
    assert "喜茶" in rec_text
    assert "玩乐" in rec_text or "娱乐" in rec_text
    
    # 3. 验证路线导航与时间成本计算
    nav = result["navigation_info"]
    assert nav is not None
    assert nav["duration_minutes"] > 0
    assert "地铁" in nav["description"] or "打车" in nav["description"]
    
    # 4. 验证本地报告生成
    report_path = Path(result["report_path"])
    assert report_path.exists()
    
    # 清理测试生成的报告文件
    if report_path.exists():
        os.remove(report_path)
