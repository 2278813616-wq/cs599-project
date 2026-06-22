# tests/test_recipe_agent.py

import pytest
import os
import time
from pathlib import Path
from src.agent.graph import FoodieGraph

@pytest.mark.asyncio
async def test_recipe_generation_flow():
    """测试家庭做菜流的完整推荐及步骤解析"""
    graph = FoodieGraph()
    user_id = f"test_recipe_user_{int(time.time())}"
    session_id = f"session-recipe-{int(time.time())}"
    
    state = {
        "session_id": session_id,
        "user_id": user_id,
        "user_input": "怎么做番茄炒蛋？",
        "current_disease": "",
        "location": "五道口"
    }
    
    result = await graph.run(state)
    rec_text = result["recommendation_text"]
    
    # 1. 验证菜谱名称和步骤返回
    assert "番茄炒蛋" in rec_text
    assert "锅" in rec_text
    
    # 2. 验证火候与物理判定标志是否输出
    assert "白烟" in rec_text or "热锅" in rec_text
    
    # 3. 验证本地报告生成
    report_path = Path(result["report_path"])
    assert report_path.exists()
    
    # 清理测试生成的报告文件
    if report_path.exists():
        os.remove(report_path)
