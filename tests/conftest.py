import os


def pytest_configure(config):
    """
    Unit tests should be deterministic and should not depend on Tavily/LLM latency.
    Runtime defaults remain online_required unless tests explicitly set this mode.
    """
    if os.getenv("RUN_ONLINE_TESTS") != "1":
        os.environ["FOOD_SEARCH_MODE"] = "offline_only"
        os.environ["MILVUS_FORCE_MOCK"] = "1"
        os.environ["AIONE_ENABLED"] = "0"
        os.environ["LLM_API_KEY"] = "your_llm_api_key_here"
        os.environ["HOME_RECIPE_PLAN_CACHE_TTL_SECONDS"] = "0"
