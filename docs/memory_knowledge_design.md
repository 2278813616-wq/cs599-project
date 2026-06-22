# SuperFoodie 记忆与知识分层设计

## 目标

SuperFoodie 的数据层分为三类：实时内容源、用户记忆、规则知识。三者职责必须分开，避免把 Milvus 当菜谱库，或把 Obsidian 当实时搜索源。

## 实时内容源

实时内容源负责发现新鲜内容。当前主路径是 Tavily + LLM 结构化提炼，目标来源包括下厨房、小红书、抖音、美团/大众点评、普通网页和视频平台公开页面。

可选增强源：

- `all-in-one-aione`：通过本地 `aione` CLI 访问小红书、抖音、微博。它不使用 API key，而是依赖登录 Cookie 和上游签名依赖。

适合存放或返回：

- 菜谱候选：菜名、配料、步骤、火候、来源 URL、来源平台。
- 餐厅候选：店名、菜系、人均、招牌菜、评价摘要、来源平台。
- 热门趋势：近期流行菜、季节食材、社交平台高频关键词。

实时内容源不负责长期用户画像，不直接决定最终推荐排序。

## Milvus

Milvus 负责用户记忆和语义检索，不负责维护完整菜谱数据库。

推荐的 Collection：

- `foodie_footprint`：用户真实选择过的菜品/餐厅，字段包含 `user_id`、`item_name`、`item_type`、`timestamp`、`metadata`、`embedding`。
- `user_profile_memory`：长期偏好，例如口味、预算、忌口、常见场景、负反馈。
- `chat_summary_memory`：会话摘要，而不是完整原始流水账。
- `live_content_cache`：可选，只缓存联网搜索结果摘要和来源 URL，便于调试和复用。

推荐用途：

- 最近 7 天去重。
- 根据用户偏好对联网候选重排。
- 找回“上次那家火锅”“昨天那个低脂菜”等模糊表达。
- 形成用户画像，例如爱辣、偏粤菜、不喜欢排队、预算 100 元以内。

禁止用途：

- 不要把用户问题写成足迹，例如“今天吃什么”。
- 不要把所有 LLM 输出原文无脑塞入向量库。
- 不要用 Milvus 代替 Tavily/小红书/抖音等实时内容源。

## Obsidian

Obsidian 负责可解释、可人工维护的规则知识图谱。它的价值是稳定、透明、可演示。

适合存放：

- 食材属性：高嘌呤、温热性、凉性、易过敏、辛辣。
- 健康规则：痛风忌高嘌呤，感冒忌温热辛辣，糖尿病控糖。
- 烹饪规则：冷冻肉焯水去腥，海鲜用姜葱料酒，热锅冷油防粘。
- 平替规则：没有生抽时用盐、少量老抽和糖调整咸鲜与颜色。
- 解释模板：为什么拦截、为什么仅警告、为什么推荐替代方案。

后续建议把 Markdown 双链升级为 Frontmatter + 双链，例如：

```yaml
---
type: ingredient
attributes:
  - 高嘌呤
contraindicated_for:
  - 痛风
---
# 啤酒
啤酒属于 [[高嘌呤]] 饮品。
```

## 推荐链路

1. 用户输入需求。
2. Query Planner 清洗并扩展查询词。
3. Tavily 等实时内容源获取候选。
4. LLM 将候选结构化为统一 schema。
5. Obsidian Graph-RAG 执行健康和烹饪规则检查。
6. Milvus 用户画像执行去重和个性化重排。
7. API 返回推荐、来源、健康解释、报告路径。

## 当前优先级

1. 保证 online_required 模式下真实联网失败时显式报错。
2. 单元测试显式使用 offline_only，不掩盖线上链路问题。
3. 前端和审计日志展示 Tavily 搜到的来源 URL。
4. 配置 `AIONE_ENABLED=1` 后，将小红书/抖音搜索结果并入联网上下文。
5. 再考虑引入 Tavily MCP 或 Firecrawl MCP 作为可插拔 provider。

## AIONE 配置

启用前置条件：

- 安装 `all-in-one-aione` Python 包。
- 安装 Node.js 16+ 和 npm。
- 运行 `aione setup`，让 Spider_XHS、DouYin_Spider 等上游依赖完成初始化。
- 从已登录浏览器复制 Cookie，写入 `.env`。

常用环境变量：

```env
AIONE_ENABLED=1
AIONE_CLI_PATH=venv\Scripts\aione.exe
AIONE_XHS_COOKIES=...
AIONE_DOUYIN_COOKIES=...
```

常用验证命令：

```bash
aione xhs note search --query "咖啡" --page 1 --output json
aione douyin work search-some-general --query "美食" --num 8 --output json
```
