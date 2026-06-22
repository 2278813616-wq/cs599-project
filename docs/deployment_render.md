# SuperFoodie Render 部署说明

## 部署模式

本项目使用 Render Blueprint 部署，配置文件为仓库根目录的 `render.yaml`。

线上默认启用小红书搜索/详情能力：

- `AIONE_ENABLED=1`
- `AIONE_CLI_PATH=aione`
- `AIONE_XHS_COMMENT_STORE_LOOKUP=0`

评论抓取保持关闭，只启用搜索和详情等主流程。小红书 Cookie 不写入仓库，部署时在 Render 控制台填写。

## 部署步骤

1. 将仓库推送到 GitHub。
2. 打开 Render Dashboard，选择 `New > Blueprint`。
3. 选择当前 GitHub 仓库，Render 会读取根目录的 `render.yaml`。
4. 在 Render 控制台填写 `sync: false` 的密钥环境变量：

```text
LLM_API_KEY
GAODE_API_KEY
GAODE_JS_API_KEY
GAODE_JS_SECURITY_CODE
AIONE_XHS_COOKIES
AIONE_XHS_PC_COOKIES
```

5. 确认非密钥变量如下：

```text
AIONE_ENABLED=1
AIONE_CLI_PATH=aione
AIONE_XHS_COMMENT_STORE_LOOKUP=0
MILVUS_FORCE_MOCK=1
FOOD_SEARCH_MODE=online_first
FOOD_SEARCH_ALLOW_FALLBACK=1
```

6. 点击 Apply 部署，等待服务变为 Live。

## Cookie 更新

小红书 Cookie 过期时，不需要修改代码或重新提交 GitHub：

1. 打开 Render 服务的 Environment 页面。
2. 更新 `AIONE_XHS_COOKIES` 和 `AIONE_XHS_PC_COOKIES`。
3. 执行 redeploy 或 restart。

## 验收接口

```text
GET /
GET /api/foodie/system/status
GET /api/foodie/map/config
POST /api/foodie/map/business-areas
```

部署后再测试一次小红书相关查询，确认线上可以调用 `aione`，并检查日志中没有打印 Cookie 内容。

## 注意事项

- 不要提交本地 `.env`。
- 不要把真实 Cookie 写进 `.env.example`、`render.yaml`、README 或任何文档。
- Render 免费实例空闲后会休眠，首次访问可能有冷启动。
- 若小红书线上不稳定，可在 Render 控制台临时把 `AIONE_ENABLED` 改为 `0` 后 redeploy。
