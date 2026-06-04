import os
import httpx
import json
from src.tools.graph_rag_retriever import GraphRAGRetriever
from src.memory.milvus_manager import MilvusManager
from src.tools.food_search import search_recipes_online, search_nearby_restaurants, get_trending_foods
from src.tools.location_map import LocationMap

class FoodieChatbot:
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY")
        self.api_base = os.getenv("LLM_API_BASE", "https://api.bianxie.ai/v1")
        self.model = os.getenv("LLM_MODEL", "deepseek-chat")
        
        # 依赖初始化
        self.retriever = GraphRAGRetriever("obsidian_vault")
        self.db = MilvusManager()
        self.map_client = LocationMap()
        
        # 判定是否启用本地离线规则引擎兜底
        self.is_offline = not bool(self.api_key) or self.api_key == "your_llm_api_key_here"
        if self.is_offline:
            print("【Chatbot 警告】未配置有效的 LLM_API_KEY，启用本地吃货规则推理引擎。")

    async def call_llm(self, messages: list[dict]) -> str:
        """调用大模型，在无 Key 时自动切换为 Mock 回复"""
        if self.is_offline:
            # 离线模拟决策
            last_message = messages[-1]["content"]
            return self._mock_llm_response(last_message)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.5
                    },
                    timeout=8.0
                )
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"【LLM 异常】大模型调用失败 ({e})，启用本地 Mock 兜底。")
            last_message = messages[-1]["content"]
            return self._mock_llm_response(last_message)

    def _mock_llm_response(self, user_input: str) -> str:
        """基于简单规则的离线吃货知识问答系统"""
        user_input_lower = user_input.lower()
        
        if "生抽" in user_input_lower:
            return "【物理平替方案】生抽没有了，可以使用少量【老抽 + 适量食盐 + 极少白糖】兑入清水代替。老抽上色，盐起咸味，糖提鲜味。"
        elif "粘锅" in user_input_lower:
            return "【主厨秘籍】要防止粘锅，请使用【热锅冷油】法：大火将炒锅烧干至锅底微微浮现【一层薄薄白烟】时，转中火，倒入冷花生油，并在油中撒入少许【食盐】，即可形成良好的物理防粘涂层。"
        elif "毛豆" in user_input_lower:
            return "【物态判定】煮毛豆时，大火烧水，待【毛豆全部漂浮在水面】且颜色呈翠绿时，说明已经彻底煮熟。此时应迅速将其捞出并投入【冰水】中冷水激凉，可保持表皮爽脆翠绿。"
        elif "怎么做" in user_input_lower or "做菜" in user_input_lower:
            return "建议选择经典的家常菜，比如【白切鸡】。如果鸡肉是冷冻过的，请在冷水下锅焯水时加入【生姜、大葱和料酒】以彻底去除冷冻肉腥味；如果是新鲜的，则只需沸水直接浸烫以保持鸡肉天然甘甜。"
        
        return f"听起来很好吃！建议您根据最近热门美食或者找店诉求，让我为您做图文规划。"

    async def get_recipe_recommendation(self, user_id: str, query: str, disease: str = "") -> dict:
        """
        家庭主厨做菜决策链路
        """
        # 1. 意图穿透判定
        force_include = False
        if "再吃" in query or "还要吃" in query or "想吃" in query:
            force_include = True
            
        # 2. Graph-RAG 安全性前置校验
        blocked_food = []
        health_explanation = "安全：暂未检测到食物相克或过敏冲突。"
        graph_path = []
        
        if disease:
            # 扫描我们要推荐的所有潜在食材，看是否被 Graph-RAG 拦截
            # 假设 query 里包含某种食材
            foods_to_check = ["牛肉", "鸭肉", "啤酒", "海鲜"]
            for food in foods_to_check:
                if food in query or food == query:
                    safety_res = self.retriever.query_diet_safety(disease, food)
                    if not safety_res["safety"]:
                        blocked_food.append(food)
                        health_explanation = safety_res["reason"]
                        graph_path = safety_res["path"]

        # 3. Milvus 近期吃过菜品去重
        recent_footprints = self.db.get_recent_footprints(user_id, days=7)
        
        # 4. 获取备选菜谱并过滤
        candidates = search_recipes_online(query)
        final_recipes = []
        
        for cand in candidates:
            name = cand["name"]
            # 如果被健康图谱拦截，直接丢弃
            if any(bf in name for bf in blocked_food):
                continue
            # 如果 7 天内吃过，且没有意图穿透，则过滤
            if name in recent_footprints and not force_include:
                continue
            final_recipes.append(cand)

        # 5. 整合推荐报告
        if not final_recipes:
            # 如果全部被拦截，给出一个安全的推荐
            safety_res = self.retriever.query_diet_safety(disease, "鸭肉")
            rec_text = f"抱歉，您想吃的食材与您的身体状况（{disease}）发生冲突被拦截。为了您的健康，饮食健康专家特推荐凉性的：【清蒸鸭肉】。"
            if not safety_res["safety"]:
                rec_text = "抱歉，由于极端的健康禁忌，饮食专家建议您今日宜清淡白粥调养。"
            
            return {
                "user_id": user_id,
                "mode": "home_cooking",
                "recommendation_text": rec_text,
                "graph_rag_path": graph_path,
                "health_explanation": health_explanation,
                "navigation_info": None
            }

        # 正常生成推荐
        main_recipe = final_recipes[0]
        # 记录本次就餐历史到 Milvus (为后续去重提供足迹数据)
        self.db.insert_footprint(user_id, main_recipe["name"], "recipe", {"calories": main_recipe["calories"]})
        
        rec_text = f"【超级吃货智能主厨推荐】为您推荐：{main_recipe['name']}。\n描述：{main_recipe['description']}\n\n"
        rec_text += "【食材清单】：\n" + "\n".join([f" - {i}" for i in main_recipe["ingredients"]]) + "\n"
        rec_text += "【调料配比】：\n" + "\n".join([f" - {c}" for c in main_recipe["condiments"]]) + "\n\n"
        rec_text += "【精细化步骤指引】：\n" + "\n".join([f"{idx+1}. {step}" for idx, step in enumerate(main_recipe["steps"])])

        return {
            "user_id": user_id,
            "mode": "home_cooking",
            "recommendation_text": rec_text,
            "graph_rag_path": graph_path,
            "health_explanation": health_explanation,
            "navigation_info": None
        }

    async def get_restaurant_recommendation(self, user_id: str, location: str, query: str, disease: str = "", budget: float = 150) -> dict:
        """
        外出探店多智能体决策链路
        """
        # 1. Graph-RAG 安全性前置校验
        blocked_items = []
        health_explanation = "安全：未检测到过敏及就餐冲突。"
        graph_path = []
        
        if disease:
            check_items = ["啤酒", "海鲜", "牛肉", "鸭肉"]
            for item in check_items:
                # 模糊检测
                if item in query:
                    safety_res = self.retriever.query_diet_safety(disease, item)
                    if not safety_res["safety"]:
                        blocked_items.append(item)
                        health_explanation = safety_res["reason"]
                        graph_path = safety_res["path"]

        # 2. 检索餐厅
        restaurants = search_nearby_restaurants(location, query, budget)
        final_rests = []
        
        # 3. 过滤被拦截的餐厅（如果含有被禁食材）
        for rest in restaurants:
            # 如果主营被健康禁忌拦截，丢弃
            if any(bi in rest["cuisine"] for bi in blocked_items):
                continue
            final_rests.append(rest)

        if not final_rests:
            return {
                "user_id": user_id,
                "mode": "dining_out",
                "recommendation_text": f"抱歉，附近在人均 {budget} 元内的 {query} 餐厅均与您的健康状况（{disease}）发生冲突被拦截。为了您的安全，建议您调整口味搜索清淡膳食。",
                "graph_rag_path": graph_path,
                "health_explanation": health_explanation,
                "navigation_info": None
            }

        # 选取评分最高的第一家做吃喝玩乐一条龙推荐
        selected_rest = final_rests[0]
        
        # 4. 调用高德路线规划计算时间成本
        nav_info = await self.map_client.get_route_duration(location, selected_rest["name"], mode="transit")
        
        # 记录足迹
        self.db.insert_footprint(user_id, selected_rest["name"], "restaurant", {"rating": selected_rest["rating"]})

        # 组装吃喝玩乐决策报告文本
        rec_text = f"【超级吃货探店一站式决策】\n为您选中：【{selected_rest['name']}】\n"
        rec_text += f" - 特色菜系: {selected_rest['cuisine']} | 平均消费: 人均 {selected_rest['avg_price']} 元\n"
        rec_text += f" - 各平台评价: {', '.join(selected_rest['platforms'])}\n"
        rec_text += f" - 招牌必吃推荐: {', '.join(selected_rest['signature_dishes'])}\n"
        rec_text += f" - 大众情感差评避坑: {selected_rest['reviews']['bad']}\n\n"
        
        rec_text += f"🎉【吃喝玩乐一条龙增值推荐】：\n"
        rec_text += f" - 饮品推荐: 步行 {selected_rest['nearby_dessert']['distance']} 至 {selected_rest['nearby_dessert']['name']} 享用 【{selected_rest['nearby_dessert']['recommended']}】\n"
        rec_text += f" - 餐后娱乐: 前往 {selected_rest['nearby_entertainment']['name']} ({selected_rest['nearby_entertainment']['distance']}) 进行 【{', '.join(selected_rest['nearby_entertainment']['activities'])}】\n"

        return {
            "user_id": user_id,
            "mode": "dining_out",
            "recommendation_text": rec_text,
            "graph_rag_path": graph_path,
            "health_explanation": health_explanation,
            "navigation_info": nav_info
        }
