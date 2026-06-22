# Architecture Specification: SuperFoodie 超级吃货智能助手

## 1. 总体架构图

系统采用严格的分层架构，确保组件单向依赖，杜绝循环引用：

```
                    ┌─────────────────────────┐
                    │       FastAPI API       │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   LangGraph Agent Core  │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      Tools Adapter      │
                    └────────────┬────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
┌────────▼────────┐     ┌────────▼────────┐     ┌────────▼────────┐
│  Graph-RAG Engine    │     │  Milvus Memory  │     │   Food MCP      │
│ (Obsidian Vault)│     │  (Vector/Scalar)│     │    Server       │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

*   **API 层** (`api/routes.py`)：接收前端请求，分发任务给 Agent 引擎，调用 PDF 渲染工具返回吃货指南。
*   **Agent 核心层** (`agent/graph.py` & `chatbot.py`)：使用 LangGraph 进行状态流转编排。
*   **Tools 适配层** (`tools/`)：向 Agent 暴露标准 python 函数，用于触发地图 API、美食搜索及图谱检索。
*   **基础设施支撑层**：
    *   **Graph-RAG Engine**：读取并分析 `obsidian_vault` 中的双链网络结构。
    *   **Milvus Database**：持久化向量特征与标量元数据。
    *   **Foodie MCP Server**：以独立进程暴露标准的 MCP Tool。

---

## 2. LangGraph 状态机与智能体路由

系统将整个吃货决策过程分解为 LangGraph 图拓扑结构。

### 2.1 全局状态定义 (AgentState)
在 `src/agent/graph.py` 中，定义全局图状态类，继承自 Pydantic 的 BaseModel：
- `user_id`: str (用户身份)
- `session_id`: str (当前会话 ID)
- `mode`: str ("home_cooking" | "dining_out")
- `user_input`: str (最新输入)
- `chat_history`: List[Dict] (对话上下文)
- `extracted_memory`: Dict (读取出的忌口、偏好与足迹)
- `route_plan`: Dict (地图出行耗时规划结果)
- `evaluation_result`: Dict (吃货指南及健康审查)
- `current_step`: str (当前做菜步骤或选店进度)
- `audit_logs`: List[Dict] (审计日志追踪)

### 2.2 节点拓扑设计
```
┌──────────────┐     ┌──────────────┐     ┌───────────────────┐
│ load_memory  ├────►│ intent_parse ├────►│  graph_rag_guard  │
└──────────────┘     └──────────────┘     └─────────┬─────────┘
                                                    │
                                                    ▼
┌──────────────┐     ┌──────────────┐     ┌───────────────────┐
│  output_pdf  │◄────┤  decide_food │◄────┤ deduplicate_filter│
└──────────────┘     └──────────────┘     └───────────────────┘
                                                   ▲
                                                   │ (Tool-Calling Loop)
                                                   ▼
                                          ┌───────────────────┐
                                          │   Tool Execution  │
                                          └───────────────────┘
```

1.  **load_memory 节点**：连接 Milvus，根据 `user_id` 查询长期偏好、忌口及就餐记录，更新至 State。
2.  **intent_parse 节点**：大厅路由 Agent 解析意图（是否提及“吃红烧肉”等穿透词，属于“做菜”还是“探店”需求）。
3.  **graph_rag_guard 节点**：读取用户的健康与疾病状态，启动 Obsidian Graph-RAG，分析是否存在食物相克或发物冲突，给出安全警告或拦截条件。
4.  **deduplicate_filter 节点**：根据 Milvus 元数据中的最近 7 天足迹，过滤候选清单（若用户直接提起则绕过）。
5.  **decide_food 节点**：`ChefAgent` 或 `GourmetAgent` 进行任务执行。在此节点中，Agent 并不硬编码调用工具，而是大模型生成 **Function Calling** 意图来选择性调用外部工具（如 Map API、在线食谱搜索），并循环调用执行。
6.  **output_pdf 节点**：调用 PDF 报表生成器生成可下载的决策文件，并追加 JSONL 审计日志。

---

## 3. Milvus 统一数据层设计

为达成大作业中“全盘采用 Milvus 存储”的要求，我们建立两个核心的 Collections：

### 3.1 `foodie_footprint` (吃货足迹表)
*   **向量字段 (Vector Field)**:
    *   `embedding`: 1536维 (基于菜名/餐厅描述文本的 Embedding 向量，便于做口味相似度匹配)。
*   **标量字段 (Scalar Fields)**:
    *   `id`: INT64 (Primary Key, AutoID=True)。
    *   `user_id`: VARCHAR (用户 ID, 带 Partition/Index)。
    *   `item_name`: VARCHAR (食物名称或餐厅名称)。
    *   `item_type`: VARCHAR ("recipe" | "restaurant")。
    *   `timestamp`: INT64 (消费时间戳，用于 7 天内去重前置过滤)。
    *   `metadata`: VARCHAR (JSON 字符串，存储具体价格、评分等数据)。

### 3.2 `chat_history` (对话历史表)
*   **向量字段 (Vector Field)**:
    *   `embedding`: 1536维 (对话内容 Embedding，以便进行历史对话的 Semantic RAG 检索)。
*   **标量字段 (Scalar Fields)**:
    *   `id`: INT64 (Primary Key, AutoID=True)。
    *   `session_id`: VARCHAR (会话 ID，建 Index，支持过滤)。
    *   `role`: VARCHAR ("user" | "assistant")。
    *   `content`: VARCHAR (文本内容)。
    *   `timestamp`: INT64 (对话时间戳)。

---

## 4. Obsidian 双链 Graph-RAG 引擎设计

在 `src/tools/graph_rag_retriever.py` 中，Graph-RAG 的构建与运行步骤如下：

```python
import os
import re
import networkx as nx

class GraphRAGRetriever:
    def __init__(self, vault_path: str):
        self.vault_path = vault_path
        self.graph = nx.DiGraph()
        self.build_graph()

    def build_graph(self):
        """扫描 obsidian_vault 下的所有 .md 文件，根据 [[双链]] 建立有向图"""
        for root, dirs, files in os.walk(self.vault_path):
            for file in files:
                if file.endswith(".md"):
                    node_name = file.replace(".md", "")
                    file_path = os.path.join(root, file)
                    
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    # 匹配双链例如 [[温热性]] 或 [[辛辣发物]]
                    links = re.findall(r"\[\[([^\]]+)\]\]", content)
                    self.graph.add_node(node_name)
                    
                    for link in links:
                        # 建立双向/有向边：实体 -> 属性
                        self.graph.add_edge(node_name, link, relation="has_property")

    def query_diet_safety(self, current_disease: str, target_food: str) -> dict:
        """
        利用 NetworkX 最短路径或连通性判断食品安全冲突。
        例如：判断 感冒.md 节点是否能通过双链连通到 牛肉.md 节点相关的属性。
        """
        # 图遍历决策逻辑
        # 若存在冲突路径，返回 {"safety": False, "reason": "...", "path": [...]}
```
通过 NetworkX 库在内存中维护图关系，能够支持亚毫秒级的 Graph 关联判定，并完美展示了 Graph-RAG 核心概念，免去了用户单独部署 Neo4j 等重量级图数据库的烦恼。
