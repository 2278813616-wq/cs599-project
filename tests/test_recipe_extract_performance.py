import asyncio
import time

import pytest

from src.tools import food_search


def _detail(idx: int) -> dict:
    return {
        "note_id": f"note-{idx}",
        "title": f"recipe note {idx}",
        "desc": " ".join(["concrete recipe text"] * 20),
        "safe_url": f"https://example.com/note/{idx}",
        "image_urls": [f"https://example.com/{idx}.webp"],
    }


@pytest.mark.asyncio
async def test_xhs_recipe_extract_runs_with_bounded_concurrency(monkeypatch):
    monkeypatch.setenv("AIONE_XHS_RECIPE_EXTRACT_LIMIT", "4")
    monkeypatch.setenv("AIONE_XHS_RECIPE_EXTRACT_CONCURRENCY", "4")

    async def fake_extract(query: str, detail: dict) -> dict:
        await asyncio.sleep(0.2)
        return {
            "name": f"{query}-{detail['note_id']}",
            "description": "fast extracted recipe",
            "ingredients": ["beef 200g", "pepper 50g"],
            "condiments": ["salt 2g", "soy sauce 10ml"],
            "steps": ["prep ingredients", "stir fry", "plate"],
            "calories": 320,
        }

    monkeypatch.setattr(food_search, "_extract_recipe_from_xhs_detail_with_llm", fake_extract)
    social_results = [{"platform": "xhs", "details": [_detail(i) for i in range(4)]}]

    started = time.perf_counter()
    recipes = await food_search._recipes_from_social_results("stir fry beef", social_results, rec_count=3)
    elapsed = time.perf_counter() - started

    assert len(recipes) == 3
    assert elapsed < 0.55
