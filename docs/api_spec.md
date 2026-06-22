# API Specification: SuperFoodie 超级吃货智能助手

## 1. Web API 端点规范

系统使用 FastAPI 暴露标准的 Web API。

### 1.1 【POST】开始就餐流 (Start Session)
*   **路径**：`/api/foodie/start`
*   **描述**：初始化当前吃货会话，读取用户长期记忆偏好，生成首轮推荐。
*   **请求体 (Request JSON)**：
    ```json
    {
      "user_id": "user_2026_test",
      "mode": "home_cooking", 
      "current_disease": "感冒咳嗽",
      "dining_people_count": 3,
      "location": "五道口",
      "budget": 150.0
    }
    ```
*   **响应体 (Response JSON)**：
    ```json
    {
      "session_id": "session-1717400000",
      "mode": "home_cooking",
      "recommendation_text": "【超级吃货智能主厨推荐】为您推荐：经典番茄炒蛋 + 香菇菜心 + 番茄蛋花汤（3人份）。\n...",
      "health_explanation": "安全：已自动过滤辛辣温热发物。",
      "graph_rag_path": [],
      "report_path": "docs/reports/session-1717400000_report.pdf",
      "image_url": "/images/classic_tomato_egg.png",
      "source": "下厨房"
    }
    ```

### 1.2 【GET】获取系统诊断状态 (System Diagnostics)
*   **路径**：`/api/foodie/system/status`
*   **描述**：获取当前系统核心连接（大模型、向量数据库、地图服务）是在线可用还是降级备用状态。
*   **响应体 (Response JSON)**：
    ```json
    {
      "llm": "online",
      "milvus": "offline_mock_json",
      "gaode": "mock_estimation",
      "platform_fetch_todo": ["小红书接口获取受限 (使用Mock数据与预置图文)"]
    }
    ```

### 1.3 【POST】多轮对话与决策 (Interact Session)
*   **路径**：`/api/foodie/{session_id}/interact`
*   **描述**：用户输入回答、中途提问或要求平替，Agent 推理后返回下一步动作。
*   **请求体 (Request JSON)**：
    ```json
    {
      "user_input": "我家里没有生抽了，可以用什么代替？"
    }
    ```
*   **响应体 (Response JSON)**：
    ```json
    {
      "action": "continue", 
      "agent_response": "生抽可用生抽平替方案：使用少量老抽加上适量食盐和极少白糖调匀代替。现在进入步骤一：热锅，待锅底微现白烟后倒入冷油并撒入少许食盐以防粘鱼皮...",
      "step_index": 1,
      "total_steps": 5,
      "health_status": "safe"
    }
    ```

### 1.3 【GET】下载 PDF 图文吃货决策书 (Download Report)
*   **路径**：`/api/foodie/{session_id}/report`
*   **描述**：面试/选店决策结束后，一键下载 PDF 图文报告。
*   **响应格式**：`application/pdf` 二进制文件流，浏览器中自动触发下载。

### 1.4 【GET】获取决策审计日志流 (Audit Trail)
*   **路径**：`/api/foodie/audit/logs`
*   **描述**：获取当前系统所有的决策审计日志列表，用于调试与可观测性。
*   **响应体 (Response JSON)**：
    ```json
    [
      {
        "timestamp": "2026-06-03T17:05:00Z",
        "session_id": "session-1717400000",
        "event": "graph_rag_check",
        "data": {
          "status": "warning",
          "blocked_items": ["牛肉", "羊肉"],
          "reason": "感冒咳嗽患者忌辛辣温热"
        }
      }
    ]
    ```

---

## 2. CLI 命令行接口规范

系统提供独立的 CLI 工具用于无 Web 界面环境下的脱机本地评测。

*   **运行命令**：`python src/main.py [options]`
*   **参数配置**：

| 参数 | 格式 | 描述 |
| :--- | :--- | :--- |
| `--interactive` | 无参数 | 启用命令行交互聊天流，直接进行模拟做菜/选店。 |
| `--user-id` | `str` | 指定用户 ID，加载特定的忌口与偏好。 |
| `--mode` | `str` | 强制指定运行模式：`home` (自己做) 或是 `out` (出去吃)。 |
| `--eval-harness`| 无参数 | 启动 Harness 评测套件，运行 `tests/` 中的 benchmark 并输出结果。 |
| `--obsidian-vault`| `path` | 指定 Obsidian 知识库的绝对路径（默认为 `obsidian_vault`）。 |
