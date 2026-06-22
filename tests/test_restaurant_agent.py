import os
import time
from pathlib import Path

import pytest

from src.agent.graph import FoodieGraph


@pytest.mark.asyncio
async def test_restaurant_recommendation_flow_returns_dynamic_candidates():
    """Dining-out recommendations are dynamic, so the test validates behavior instead of one fixed shop."""
    graph = FoodieGraph()
    user_id = f"test_restaurant_user_{int(time.time())}"
    session_id = f"session-rest-{int(time.time())}"

    state = {
        "session_id": session_id,
        "user_id": user_id,
        "mode": "dining_out",
        "user_input": "想在商圈附近吃川菜",
        "current_disease": "",
        "location": "武汉梦时代",
        "budget": 120,
        "dining_people_count": 2,
        "business_area_context": {
            "name": "武汉梦时代",
            "address": "武汉市武昌区",
            "radius": 5000,
        },
    }

    result = await graph.run(state)

    assert result["recommendation_text"].strip()
    assert isinstance(result.get("recommendations"), list)
    assert result["recommendations"], "dining-out flow should return at least one restaurant candidate"
    assert result["navigation_info"] is None

    first = result["recommendations"][0]
    assert first.get("name") or first.get("store_name")
    assert first.get("address") or first.get("description")

    report_path = Path(result["report_path"])
    assert report_path.exists()
    if report_path.exists():
        os.remove(report_path)
