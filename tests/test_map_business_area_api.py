from fastapi.testclient import TestClient

import src.api.routes as routes


def _fake_poi(name: str, idx: int) -> dict:
    return {
        "id": f"poi-{idx}",
        "name": name,
        "type": "璐墿鏈嶅姟;鍟嗗満;璐墿涓績",
        "typecode": "060101",
        "address": "test address",
        "adname": "test district",
        "cityname": "test city",
        "location": f"114.33{idx},30.51{idx}",
        "distance": str(100 + idx),
        "biz_ext": {"rating": "4.8"},
    }


def test_business_area_endpoint_is_slim_and_cached(monkeypatch, tmp_path):
    calls = []

    async def fake_amap_get(client, path, params):
        calls.append((path, params.get("keywords")))
        return [_fake_poi("缇ゅ厜骞垮満", len(calls))]

    async def fake_geocode(client, address, city):
        return (114.33, 30.51)

    monkeypatch.setattr(routes, "MAP_BUSINESS_AREA_CACHE", tmp_path / "map_cache.json")
    monkeypatch.setattr(routes, "_amap_get", fake_amap_get)
    monkeypatch.setattr(routes, "_amap_geocode", fake_geocode)

    client = TestClient(routes.app)
    payload = {
        "query": "武汉梦时代",
        "city": "武汉",
        "radius": 5000,
        "user_id": "pytest-user",
        "explore_new": False,
    }

    first = client.post("/api/foodie/map/business-areas", json=payload)
    assert first.status_code == 200
    first_data = first.json()
    assert first_data["cache_hit"] is False
    assert first_data["raw_count"] >= 1
    assert first_data["raw_results"] == []
    assert first_data["excluded_results"] == []
    assert len(calls) > 0

    calls.clear()
    second = client.post("/api/foodie/map/business-areas", json=payload)
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["cache_hit"] is True
    assert calls == []

    debug_payload = {**payload, "debug": True}
    debug = client.post("/api/foodie/map/business-areas", json=debug_payload)
    assert debug.status_code == 200
    debug_data = debug.json()
    assert debug_data["cache_hit"] is True
    assert debug_data["raw_results"], "debug=true should expose cached raw Gaode-shaped data"
