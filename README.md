# SuperFoodie 超级吃货智能助手 (cs599-project)

> AI 驱动的家庭主厨与探店吃喝玩乐决策智能体系统  
> **CS599 企业级应用软件设计与开发** | 期末大作业交付仓库

---

## 📋 项目简介

**SuperFoodie** 是一个为美食爱好者打造的企业级 AI 智能体系统。系统包含两大核心业务功能：
1. **自己做 (Home Chef)**：结合用户的身体状况、近期目标（如减脂/增压）与忌口，利用基于 Obsidian 的 **Graph-RAG (知识图谱检索增强)** 过滤禁忌食材并推荐科学营养的菜单，提供带有物态变化物理标志判断的精细烹饪指导。
2. **出去吃 (Gourmet Explorer)**：根据用户的预算、口味及同行人数，多渠道抓取餐厅评价，整合高德地图出行路线及时间成本，并在就餐后搭配周边娱乐（电影、台球等）形成吃喝玩乐一条龙方案，最终输出精美的图文 PDF 决策报告。

---

## 📐 技术栈

- **AI IDE**: Antigravity
- **LLM**: DeepSeek-Chat (DeepSeek-V3 兼容接口)
- **Agent 框架**: LangGraph / LangChain
- **知识图谱**: Obsidian (Markdown 双链结构)
- **数据与记忆数据库**: Milvus (向量数据库)
- **报告生成**: ReportLab (图文 PDF 渲染)
- **地图服务**: 高德地图 API
- **容器化**: Docker & Docker-compose

---

## 📁 目录结构

```
cs599-project/
├── docs/                        # SDD 规格说明文档
│   ├── CS599_大作业报告.pdf        # 最终大作业报告 PDF（含图文）
│   ├── product_spec.md          # 产品规格说明书 (包含 Graph-RAG 交互、去重与热点检索逻辑)
│   ├── architecture_spec.md     # 架构规格说明书 (双 Agent 拓扑、Milvus Collection 设计与 Obsidian 双链图谱设计)
│   └── api_spec.md              # 接口规格说明书 (API 参数与返回格式)
├── obsidian_vault/              # 本地 Obsidian 知识图谱库 (Markdown 双链)
│   ├── 食物属性/
│   │   ├── 牛肉.md              # 包含 [[温热性]]
│   │   └── 鸭肉.md              # 包含 [[凉性]]
│   └── 健康状况/
│       ├── 感冒.md              # 包含 [[忌温热性]]
│       └── 减脂期.md            # 包含 [[宜低脂肪高蛋白]]
├── src/
│   ├── agent/
│   │   ├── graph.py             # LangGraph 状态机编排
│   │   └── chatbot.py           # 核心吃货对话路由与意图穿透
│   ├── tools/
│   │   ├── graph_rag_retriever.py # Graph-RAG 引擎：解析 Obsidian 双链构建食物图谱
│   │   ├── location_map.py      # 地图导航工具
│   │   ├── food_search.py       # 大众点评、社交热点多渠道搜索工具
│   │   └── report_generator.py  # 渲染 PDF 图文报告
│   ├── harness/
│   │   ├── quality_check.py     # 列表重复方差检查 (方差 < 10)
│   │   ├── output_validator.py  # Schema 验证器
│   │   └── audit_log.py         # JSONL 决策审计日志 (追加写模式)
│   ├── memory/
│   │   └── milvus_manager.py    # 纯 Milvus 统一存储库 (就餐历史/对话历史)
│   ├── mcp_server/
│   │   └── foodie_mcp.py        # 吃货专用 MCP Server
│   └── api/
│       ├── routes.py            # FastAPI API 端点
│       └── static/
│           └── index.html       # 吃货单页面前端 UI
├── tests/
│   ├── test_harness.py          # 评估方差、置信度与 Schema 验证测试
│   ├── test_graph_rag.py        # 测试 Graph-RAG 食物属性拦截逻辑
│   ├── test_deduplication.py    # 测试近期就餐去重机制及主动提起穿透机制
│   └── test_restaurant_agent.py # 找店与探店规划测试
├── docker-compose.yml           # 启动 FastAPI 与 Milvus 镜像
├── requirements.txt
├── .gitignore
├── .env.example                 # 安全密钥配置模板
└── README.md
```

---

## 🚀 环境搭建与运行

### 1. 启动本地 Milvus 向量库
确保本地电脑已安装 Docker 与 Docker-compose，在项目根目录下运行：
```bash
docker-compose up -d
```
验证服务启动正常，Milvus 监听端口 `19530`。

### 2. 依赖安装与密钥配置
```bash
# 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows 激活命令

# 安装 Python 包
pip install -r requirements.txt

# 配置环境变量
copy .env.example .env
# 编辑 .env 文件，填入您的 LLM_API_KEY
```

### 3. 启动 API 服务
```bash
uvicorn src.api.routes:app --reload
```
访问本地前端界面进行交互：http://localhost:8000

---

## 🎯 项目里程碑状态

- [x] **Milestone 1: Proposal** (~06.01) - 系统设计、架构图与 SDD 规格说明书初稿
- [ ] **Milestone 2: MVP** (~06.08) - 核心闭环做菜与找店功能联调，通过 Harness 自动化验证
- [ ] **Milestone 3: Final** (~06.22) - 云端部署，一键打包运行，提交最终报告 PDF
