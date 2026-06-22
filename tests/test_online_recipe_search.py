import os

import pytest

from src.tools.food_search import search_recipes_online


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ONLINE_TESTS") != "1",
    reason="Set RUN_ONLINE_TESTS=1 to verify real Tavily/LLM recipe search.",
)


@pytest.mark.asyncio
async def test_online_recipe_search_uses_real_sources(monkeypatch):
    monkeypatch.setenv("FOOD_SEARCH_MODE", "online_required")
    recipes = await search_recipes_online("番茄炒蛋", rec_count=1)

    assert recipes
    assert "番茄" in recipes[0]["name"] or "番茄" in recipes[0]["description"]
    assert recipes[0].get("source")
    assert recipes[0].get("steps")

