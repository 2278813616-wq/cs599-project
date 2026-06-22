from pathlib import Path

from src.tools.report_generator import FoodieReportGenerator


def test_report_generator_creates_home_cooking_report(tmp_path):
    generator = FoodieReportGenerator()
    output = tmp_path / "home_report.pdf"
    path = generator.generate_report(
        "pytest-home-session",
        {
            "user_id": "pytest-user",
            "mode": "home_cooking",
            "time": "2026-06-21 12:00:00",
            "selected_recommendations": [
                {
                    "name": "辣子鸡",
                    "description": "香辣下饭",
                    "ingredients": ["鸡腿肉", "干辣椒"],
                    "condiments": ["盐", "生抽"],
                    "steps": ["切块腌制", "炸香", "回锅炒匀"],
                }
            ],
            "health_explanation": "安全：未发现冲突。",
            "graph_rag_path": [],
        },
        str(output),
    )
    assert Path(path).exists()
    assert Path(path).stat().st_size > 0


def test_report_generator_creates_dining_out_report(tmp_path):
    generator = FoodieReportGenerator()
    output = tmp_path / "dining_report.pdf"
    path = generator.generate_report(
        "pytest-dining-session",
        {
            "user_id": "pytest-user",
            "mode": "dining_out",
            "time": "2026-06-21 12:00:00",
            "selected_recommendation": {
                "name": "川菜馆",
                "store_name": "川菜馆",
                "recommended_dishes": "辣子鸡、毛血旺",
                "address": "商圈 6 楼",
                "rating": "4.6",
                "after_meal_places": [{"name": "电影院", "description": "饭后看电影"}],
            },
            "after_meal_places": [{"name": "电影院", "description": "饭后看电影"}],
            "health_explanation": "安全：按需避开辛辣。",
            "graph_rag_path": [],
        },
        str(output),
    )
    assert Path(path).exists()
    assert Path(path).stat().st_size > 0
