import urllib.parse
import httpx
import os

# 本地美食与餐厅数据库（Mock 备用数据，用于在没有网络或真实 API 时提供稳定饱满的返回）
TRENDING_FOODS = [
    {"name": "蒜蓉小龙虾", "category": "夜市热点", "season": "夏季", "rating": 4.8},
    {"name": "冰镇酸梅汤", "category": "消暑甜品", "season": "夏季", "rating": 4.9},
    {"name": "羊肉炉", "category": "滋补火锅", "season": "冬季", "rating": 4.7},
    {"name": "大闸蟹", "category": "时令海鲜", "season": "秋季", "rating": 4.9},
    {"name": "椰子鸡", "category": "清淡养生", "season": "四季", "rating": 4.6},
]

MOCK_RECIPES = {
    "番茄炒蛋": {
        "name": "经典番茄炒蛋",
        "category": "家常菜",
        "description": "酸甜可口，营养丰富，超级下饭的国民家常菜。",
        "ingredients": ["西红柿 2个", "鸡蛋 3个"],
        "condiments": ["油 15ml", "食白糖 5g", "食盐 3g", "小葱 1根"],
        "steps": [
            "【新鲜度处理】检查鸡蛋壳是否干净完整，番茄外表红润无软烂，说明食材新鲜。",
            "【物态判断-热锅】大火烧热空锅，待锅底微微浮现【一层薄薄白烟】时，转中火，倒入冷油并撒入极少许【食盐】（物理防粘）。",
            "【炒蛋】倒入打散的蛋液。蛋液边缘遇热油迅速【膨胀起泡】时，用筷子快速划散至金黄，立即盛出（保持鲜嫩）。",
            "【炒番茄】锅中余油，倒入切好块的番茄大火翻炒，加入白糖，待番茄汁炒出【浓稠浆糊状】时，倒入鸡蛋合炒。",
            "【出锅】翻炒均匀，撒入葱花，起锅装盘。"
        ],
        "calories": 250
    },
    "白切鸡": {
        "name": "正宗广式白切鸡",
        "category": "粤菜",
        "description": "皮脆肉嫩，原汁原味，佐以姜葱茸，鲜美无比。",
        "ingredients": ["三黄鸡 半只", "小葱 3根", "生姜 1块"],
        "condiments": ["花生油 20ml", "食盐 5g", "料酒 10ml"],
        "steps": [
            "【去腥处理-新鲜度判断】检查鸡肉，若鸡皮光滑且无异味说明新鲜，仅需冷水焯水；若为冷冻鸡，焯水时必须加入【葱姜和料酒】以彻底除腥。",
            "【物态判断-三提三浸】大锅烧水，水温达到【即将沸腾但未大滚】（锅底有细密小气泡不断升起）时，提鸡头，将鸡身浸入水中5秒，提起沥水。重复三次，以使鸡皮遇热迅速收紧定型。",
            "【小火慢炖】将鸡浸入水中，盖上锅盖，转微火（保持水面【微沸不滚】，仅有少量气泡浮出），慢炖20分钟。",
            "【过冷水】捞出鸡肉，立刻投入冰水中浸泡10分钟（使鸡皮与皮下脂肪遇冷凝结成爽脆的皮冻）。",
            "【斩件】沥干水分，斩件摆盘，佐以热花生油淋熟的姜葱茸蘸料。"
        ],
        "calories": 380
    },
    "香菇菜心": {
        "name": "香菇蚝油菜心",
        "category": "家常菜",
        "description": "翠绿爽口，香菇滑嫩，蚝油香浓。",
        "ingredients": ["菜心 300g", "鲜香菇 6个"],
        "condiments": ["蚝油 15ml", "大蒜 3瓣", "生粉 5g", "油 10ml"],
        "steps": [
            "【焯水物态】大锅烧水，水开后加入 1g 盐和 3ml 油（保持菜心翠绿）。放入菜心焯水 1 分钟，待叶子【变深绿变软】立刻捞出摆盘。",
            "【炒香菇】热锅凉油，下蒜末爆香，放入切片的香菇大火翻炒至【微软出水】。",
            "【蚝油汁】加入蚝油、生抽和少量水煮开，淋入水淀粉，待芡汁【起泡变浓稠】时，淋在菜心上。"
        ],
        "calories": 120
    }
}

MOCK_RESTAURANTS = [
    {
        "id": "r_01",
        "name": "五道口老川鲁火锅",
        "cuisine": "川菜/火锅",
        "location": "海淀区五道口商业街3号",
        "rating": 4.7,
        "avg_price": 95,
        "signature_dishes": ["冷锅鲜毛肚", "麻辣九宫格", "现炸酥肉"],
        "reviews": {
            "good": "毛肚非常爽脆，味道很正宗，分量挺足！",
            "bad": "环境有点吵，晚上排队等了一个多小时，体验一般。"
        },
        "platforms": ["大众点评 4.7分", "美团 4.6分", "抖音美食 4.8分"]
    },
    {
        "id": "r_02",
        "name": "知春里广式点心局",
        "cuisine": "粤菜/点心",
        "location": "海淀区知春路地铁A口旁",
        "rating": 4.8,
        "avg_price": 85,
        "signature_dishes": ["招牌水晶虾饺", "金牌红米肠", "杨枝甘露"],
        "reviews": {
            "good": "虾饺里有整颗虾仁，很Q弹！服务态度特别好。",
            "bad": "店面比较小，周末中午来排不上队，甜点偏甜了点。"
        },
        "platforms": ["大众点评 4.8分", "美团 4.8分"]
    }
]

def get_trending_foods(season: str = "夏季") -> list[dict]:
    """获取当前时令火热的美食排行榜"""
    return [food for food in TRENDING_FOODS if food["season"] == season or food["season"] == "四季"]

def search_recipes_online(query: str) -> list[dict]:
    """
    在线搜索菜谱信息 (包含新鲜度、火候物态、平替等细节指导)
    """
    results = []
    query_lower = query.lower()
    
    # 模拟从我们的高精细数据库检索
    for name, recipe in MOCK_RECIPES.items():
        if query_lower in name or query_lower in recipe["category"].lower() or query_lower in recipe["description"].lower():
            results.append(recipe)
            
    # 若本地无硬编码，则生成一个基于标准模版的动态菜谱模拟
    if not results:
        results.append({
            "name": f"秘制{query}",
            "category": "创意料理",
            "description": f"一份关于{query}的美味创意烹饪方案。",
            "ingredients": [f"{query} 200g"],
            "condiments": ["油 10ml", "盐 3g", "葱姜 适量"],
            "steps": [
                f"【新鲜度处理】检查{query}的外观和气味，无异味说明新鲜，无需过多去腥处理。",
                "【物态判断-热锅凉油】大火热锅，至【有轻微白烟冒出】时倒冷油，使食材受热均匀。",
                f"【炒制】放入{query}大火快速翻炒，至【颜色变深且变软】时加入调料调味。",
                "【起锅】收汁装盘。"
            ],
            "calories": 300
        })
    return results

def search_nearby_restaurants(location: str, query: str, budget: float = 150) -> list[dict]:
    """
    检索附近餐厅及玩乐项目。
    参数:
    - location: 定位中心点
    - query: 找店关键字 (如 川菜, 火锅)
    - budget: 人均预算限制
    """
    matched = []
    query_lower = query.lower()
    
    for rest in MOCK_RESTAURANTS:
        # 匹配口味且在预算内
        if (query_lower in rest["cuisine"].lower() or query_lower in rest["name"].lower()) and rest["avg_price"] <= budget:
            # 自动搭配周边的娱乐项目和甜点
            # 这是一个吃喝玩乐一条龙的模拟
            extended_info = rest.copy()
            extended_info["nearby_dessert"] = {
                "name": "喜茶(五道口店)",
                "distance": "120m",
                "recommended": "多肉葡萄, 芝芝绿妍"
            }
            extended_info["nearby_entertainment"] = {
                "name": "万达影城(五道口店)" if "五道口" in rest["location"] else "腾讯视频好时光微影院",
                "distance": "350m",
                "activities": ["看电影", "打台球"]
            }
            matched.append(extended_info)
            
    return matched
