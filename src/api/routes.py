from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import uuid
import time
from typing import Optional, List

from src.agent.graph import FoodieGraph
from src.harness.audit_log import AuditLogger

app = FastAPI(title="SuperFoodie 超级吃货智能助手 API", version="1.0")

# 初始化共享状态机引擎
graph_engine = FoodieGraph()

# 静态对话历史临时索引 (session_id -> latest_state)
active_sessions = {}

# Pydantic 数据模型定义

class StartRequest(BaseModel):
    user_id: str
    mode: str  # "home_cooking" | "dining_out"
    current_disease: Optional[str] = ""
    location: Optional[str] = "我的位置"
    budget: Optional[float] = 150.0

class StartResponse(BaseModel):
    session_id: str
    mode: str
    recommendation_text: str
    health_explanation: str
    graph_rag_path: List[str]
    report_path: str

class InteractRequest(BaseModel):
    user_input: str

class InteractResponse(BaseModel):
    agent_response: str
    report_path: str

# API 路由端点

@app.post("/api/foodie/start", response_model=StartResponse)
async def start_foodie_flow(req: StartRequest):
    """
    初始化吃货助理流，前置进行长期偏好提取、健康图谱校验并启动状态机。
    """
    session_id = f"session-{uuid.uuid4().hex[:8]}-{int(time.time())}"
    
    # 构建初始状态
    initial_state = {
        "session_id": session_id,
        "user_id": req.user_id,
        "mode": req.mode,
        "current_disease": req.current_disease,
        "location": req.location,
        "budget": req.budget,
        "user_input": "推荐今日特色菜" if req.mode == "home_cooking" else "推荐附近的特色餐馆"
    }
    
    # 实例化审计日志记录器
    logger = AuditLogger(session_id)
    logger.log("session_start", {"req": req.dict()})
    
    try:
        # 运行 LangGraph / 模拟状态机
        final_state = await graph_engine.run(initial_state)
        
        # 存储会话快照
        active_sessions[session_id] = final_state
        
        logger.log("session_initialized", {
            "mode": final_state.get("mode"),
            "health_explanation": final_state.get("health_explanation"),
            "report_path": final_state.get("report_path")
        })
        
        return StartResponse(
            session_id=session_id,
            mode=final_state.get("mode"),
            recommendation_text=final_state.get("recommendation_text", "无法生成推荐"),
            health_explanation=final_state.get("health_explanation", ""),
            graph_rag_path=final_state.get("graph_rag_path", []),
            report_path=final_state.get("report_path", "")
        )
    except Exception as e:
        logger.log("session_start_failed", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"启动吃货流程失败: {str(e)}")


@app.post("/api/foodie/{session_id}/interact", response_model=InteractResponse)
async def interact_foodie_flow(session_id: str, req: InteractRequest):
    """
    与吃货助手进行多轮对话，支持食材平替、物态追问、娱乐推荐等深度问答。
    """
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
        
    last_state = active_sessions[session_id]
    logger = AuditLogger(session_id)
    logger.log("user_interaction", {"user_input": req.user_input})
    
    try:
        # 将用户对话记录进 Milvus 历史
        graph_engine.bot.db.insert_chat(session_id, "user", req.input_to_text(req.user_input))
        
        # 根据用户当前提问，由 Chatbot 执行决策
        # 如果是关于做菜步骤的疑问，直接进入平替与火候推理
        messages = [
            {"role": "user", "content": req.user_input}
        ]
        
        # 获取 Chatbot 响应
        response_text = await graph_engine.bot.call_llm(messages)
        
        # 将回复记录进 Milvus 历史
        graph_engine.bot.db.insert_chat(session_id, "assistant", response_text)
        
        logger.log("agent_response_success", {"response": response_text})
        
        return InteractResponse(
            agent_response=response_text,
            report_path=last_state.get("report_path", "")
        )
    except Exception as e:
        logger.log("interaction_failed", {"error": str(e)})
        raise HTTPException(status_code=500, detail=f"交互处理失败: {str(e)}")

    def input_to_text(self, text):
        return text


@app.get("/api/foodie/{session_id}/report")
async def get_foodie_report(session_id: str):
    """
    获取就餐决策报告。支持下载精美渲染的 PDF 报告。
    """
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="会话已过期，报告无法找回")
        
    state = active_sessions[session_id]
    report_path = state.get("report_path")
    
    if not report_path or not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="报告文件尚未生成或已被移除")
        
    # 判断生成的是 PDF 还是降级后的 Markdown，返回对应的媒体类型
    media_type = "application/pdf" if report_path.endswith(".pdf") else "text/markdown"
    filename = f"SuperFoodie_Report_{session_id}" + (".pdf" if report_path.endswith(".pdf") else ".md")
    
    return FileResponse(
        path=report_path,
        media_type=media_type,
        filename=filename
    )


@app.get("/api/foodie/audit/logs")
async def get_audit_logs(session_id: Optional[str] = None):
    """
    可观测性端点：获取追加写审计日志流，支持按 session_id 过滤。
    """
    log_file = "logs/audit.jsonl"
    if not os.path.exists(log_file):
        return JSONResponse(content=[])
        
    logs = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if session_id:
                    if entry.get("session_id") == session_id:
                        logs.append(entry)
                else:
                    logs.append(entry)
        return JSONResponse(content=logs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取审计日志失败: {str(e)}")


# 挂载单页面 UI 静态目录
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
else:
    print(f"【FastAPI 警告】未找到静态文件目录 '{static_dir}'，请确保 index.html 存在。")
