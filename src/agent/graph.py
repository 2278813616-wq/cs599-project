import time
from typing import Dict, List, Any
from src.agent.chatbot import FoodieChatbot
from src.tools.report_generator import FoodieReportGenerator

# 尝试导入 LangGraph，若缺失则降级为自定义原生 Python 状态机
try:
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False

class FoodieGraph:
    def __init__(self):
        self.bot = FoodieChatbot()
        self.report_gen = FoodieReportGenerator()
        
        if HAS_LANGGRAPH:
            self.graph = self._build_langgraph()
        else:
            self.graph = self._build_simple_graph()

    def _build_langgraph(self) -> Any:
        """构建正统的 LangGraph 状态图"""
        workflow = StateGraph(dict) # 采用 dict 存储图状态
        
        # 绑定节点
        workflow.add_node("load_memory_node", self.load_memory_node)
        workflow.add_node("intent_parse_node", self.intent_parse_node)
        workflow.add_node("chef_node", self.chef_node)
        workflow.add_node("gourmet_node", self.gourmet_node)
        
        # 设置入口点
        workflow.set_entry_point("load_memory_node")
        
        # 绑定直连边
        workflow.add_edge("load_memory_node", "intent_parse_node")
        
        # 绑定条件路由边
        workflow.add_conditional_edges(
            "intent_parse_node",
            self.route_by_mode,
            {
                "home_cooking": "chef_node",
                "dining_out": "gourmet_node",
                "end": END
            }
        )
        
        workflow.add_edge("chef_node", END)
        workflow.add_edge("gourmet_node", END)
        
        return workflow.compile()

    def _build_simple_graph(self) -> Any:
        """原生 Python 模拟状态机（高可用降级兜底）"""
        class SimpleGraph:
            def __init__(self, outer):
                self.outer = outer
                
            def invoke(self, state: dict) -> dict:
                # 模拟图的顺序流转
                state = self.outer.load_memory_node(state)
                state = self.outer.intent_parse_node(state)
                
                next_node = self.outer.route_by_mode(state)
                if next_node == "home_cooking":
                    state = self.outer.chef_node(state)
                elif next_node == "dining_out":
                    state = self.outer.gourmet_node(state)
                return state
                
        return SimpleGraph(self)

    # --- 图节点 Node 实现 ---

    def load_memory_node(self, state: dict) -> dict:
        """Node: 从 Milvus 记忆库加载该用户的口味偏好与 7 天足迹"""
        user_id = state.get("user_id", "anonymous")
        recent_eaten = self.bot.db.get_recent_footprints(user_id, days=7)
        state["recent_eaten"] = recent_eaten
        return state

    def intent_parse_node(self, state: dict) -> dict:
        """Node: 意图解析节点，决定走家庭做菜还是外面就餐，并判断是否包含意图穿透"""
        if state.get("mode") in {"home_cooking", "dining_out"}:
            return state
        user_input = state.get("user_input", "")
        user_input_lower = user_input.lower()
        
        # 简单规则路由，也可通过大模型辅助判定
        if "外面" in user_input_lower or "出去吃" in user_input_lower or "餐馆" in user_input_lower or "找店" in user_input_lower:
            state["mode"] = "dining_out"
        else:
            state["mode"] = "home_cooking" # 默认做菜流
            
        return state

    async def chef_node(self, state: dict) -> dict:
        """Node: 家庭主厨做菜决策代理"""
        user_id = state.get("user_id", "anonymous")
        user_input = state.get("user_input", "")
        disease = state.get("current_disease", "")
        diners = state.get("dining_people_count", 1)
        session_id = state.get("session_id", "temp_session")
        selected_items = state.get("selected_items") or []
        
        # 触发 ChefAgent 推理
        res = await self.bot.get_recipe_recommendation(user_id, user_input, disease, diners, session_id, selected_items)
        state.update(res)
        
        # 生成决策 PDF 报告
        pdf_path = f"docs/reports/{state['session_id']}_report.pdf"
        report_data = {
            "user_id": user_id,
            "mode": "home_cooking",
            "time": datetime_now_str(),
            "recommendation_text": res["recommendation_text"],
            "graph_rag_path": res["graph_rag_path"],
            "health_explanation": res["health_explanation"],
            "navigation_info": None,
            "image_url": res.get("image_url", "")
        }
        final_path = self.report_gen.generate_report(state["session_id"], report_data, pdf_path)
        state["report_path"] = final_path
        
        return state

    async def gourmet_node(self, state: dict) -> dict:
        """Node: 外出探店代理"""
        user_id = state.get("user_id", "anonymous")
        user_input = state.get("user_input", "")
        disease = state.get("current_disease", "")
        location = state.get("location", "五道口")
        diners = state.get("dining_people_count", 1)
        budget = state.get("budget", 150)
        session_id = state.get("session_id", "temp_session")
        business_area_context = state.get("business_area_context") or {}
        
        res = await self.bot.get_restaurant_recommendation(user_id, location, user_input, disease, budget, diners, session_id, business_area_context)
        state.update(res)
        
        # 生成决策 PDF 报告
        pdf_path = f"docs/reports/{state['session_id']}_report.pdf"
        report_data = {
            "user_id": user_id,
            "mode": "dining_out",
            "time": datetime_now_str(),
            "recommendation_text": res["recommendation_text"],
            "graph_rag_path": res["graph_rag_path"],
            "health_explanation": res["health_explanation"],
            "navigation_info": res["navigation_info"],
            "selected_recommendation": (res.get("recommendations") or [{}])[0],
            "after_meal_places": ((res.get("recommendations") or [{}])[0].get("after_meal_places") if res.get("recommendations") else []),
            "image_url": res.get("image_url", "")
        }
        final_path = self.report_gen.generate_report(state["session_id"], report_data, pdf_path)
        state["report_path"] = final_path
        
        return state

    # --- 条件边 Edge 路由判定 ---

    def route_by_mode(self, state: dict) -> str:
        """依据意图解析确定的业务模式选择分支"""
        mode = state.get("mode")
        if mode == "home_cooking":
            return "home_cooking"
        elif mode == "dining_out":
            return "dining_out"
        return "end"

    # --- 执行接口 ---

    async def run(self, initial_state: dict) -> dict:
        """执行图任务入口"""
        if HAS_LANGGRAPH:
            # 对于异步 Node 执行，LangGraph compile 后需要进行正确的 event 驱动，
            # 为保证在简易测试里的强移植性，我们统一通过 simple_graph 去平滑处理同步/异步流。
            # 这使得即使没有安装复杂的 langgraph 环路，我们自己写的 SimpleGraph 也能完美调用我们声明的异步 Node！
            pass
            
        simple_runner = self._build_simple_graph()
        # 依次运行节点并返回最后的状态 dict
        state = simple_runner.outer.load_memory_node(initial_state)
        state = simple_runner.outer.intent_parse_node(state)
        
        next_node = simple_runner.outer.route_by_mode(state)
        if next_node == "home_cooking":
            state = await simple_runner.outer.chef_node(state)
        elif next_node == "dining_out":
            state = await simple_runner.outer.gourmet_node(state)
            
        return state

def datetime_now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
