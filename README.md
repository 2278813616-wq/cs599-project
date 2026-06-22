# SuperFoodie 超级吃货智能助手

CS599 企业级应用软件设计与开发期末项目。项目方向为“方向一：Agentic AI 原生开发”，围绕“自己做”和“出去吃”两类美食决策场景，构建一个带工具调用、Graph-RAG、长期记忆、地图检索和 PDF 导出的美食决策 Agent。

## 当前状态

- 本地 Demo：FastAPI + 单页前端，可完成偏好输入、菜品/餐厅候选、详情查看、PDF 导出。
- Agent 能力：LangGraph 风格状态编排，按场景调用小红书/高德/Graph-RAG/Milvus/ReportLab 工具。
- 风控策略：小红书评论抓取默认关闭，线上 Cookie 只通过 Render 环境变量填写，不写入仓库。
- 性能策略：高德商圈检索支持本地缓存，默认响应不返回大体积 raw/debug 数据。
- 课程交付：`docs/` 目录仅保留最终版报告 PDF，PDF 已包含可用导航窗格（书签/大纲），方便评阅翻阅。

## 目录

```text
docs/
  CS599_大作业报告.pdf
src/
  agent/              # Agent 状态编排与业务决策
  api/                # FastAPI 路由与静态前端
  memory/             # Milvus 记忆层
  tools/              # 小红书、高德、Graph-RAG、PDF 等工具
  mcp_server.py       # SuperFoodie MCP stdio server
tests/                # 离线可重复测试
scripts/
  generate_coursework_report.py
  test_mcp_stdio.py
render.yaml           # Render 混合模式部署蓝图
```

## 本地运行

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

按需填写 `.env` 中的 `LLM_API_KEY`、`GAODE_API_KEY`、`GAODE_JS_API_KEY` 和 `GAODE_JS_SECURITY_CODE`。小红书 Cookie 不要提交到仓库；本地调试可写入 `.env`，线上部署时只在 Render 控制台环境变量中填写。

启动服务：

```powershell
uvicorn src.api.routes:app --host 127.0.0.1 --port 8003 --reload
```

访问：

```text
http://127.0.0.1:8003
```

## 测试

默认测试不依赖真实小红书、Tavily 或 Milvus：

```powershell
pytest
```

MCP smoke test：

```powershell
python scripts/test_mcp_stdio.py
```

报告生成：

```powershell
python scripts/generate_coursework_report.py
```

## MCP 加分项

`src/mcp_server.py` 提供 stdio JSON-RPC MCP Server，当前暴露的核心工具包括：

- `query_diet_safety`：基于 Graph-RAG 检查疾病/食物安全性。
- `search_recipes_online`：联网或缓存菜谱检索。
- `get_route_duration`：高德出行时间估计。

课程报告中使用 `scripts/test_mcp_stdio.py` 的输出作为 MCP 协议演示证据。

## Render 部署

本仓库提供 `render.yaml`。建议采用“混合模式”：

- 高德 API 使用真实 Key。
- LLM 可使用真实 Key。
- 小红书搜索/详情线上默认启用，Cookie 只在 Render 控制台环境变量中填写。
- `FOOD_SEARCH_MODE=online_first`，必要时允许本地缓存降级。

Render 环境变量应在控制台填写，不要提交 `.env`：

```text
LLM_API_KEY
GAODE_API_KEY
GAODE_JS_API_KEY
GAODE_JS_SECURITY_CODE
AIONE_XHS_COOKIES
AIONE_XHS_PC_COOKIES
```

GitHub Pages 只能托管静态页面，不能直接运行 FastAPI 后端；本项目后端部署优先使用 Render。

## CS599 报告交付物

- 最终 PDF：`docs/CS599_大作业报告.pdf`
- PDF 要求：已写入书签/大纲导航，评阅时可通过 PDF 导航窗格跳转章节。
- 说明：`docs/` 目录只保留最终 PDF，其余中间文档、图表素材和截图不再作为仓库交付物提交。

## 安全说明

- `.env`、运行日志、本地缓存、Milvus 数据卷不应提交。
- 小红书评论抓取默认关闭：`AIONE_XHS_COMMENT_STORE_LOOKUP=0`。
- 商圈 debug 原始 JSON 只有在请求 `/api/foodie/map/business-areas` 时传 `debug=true` 才返回。
