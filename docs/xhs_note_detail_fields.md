# 小红书帖子详情字段说明

本文记录当前 SuperFoodie 从小红书帖子详情接口中使用的数据范围。由于账号已收到平台警告，近期默认不再请求评论区接口。

## 当前调用范围

当前只使用：

```text
aione xhs note search
aione xhs note info
```

近期默认不使用：

```text
aione xhs note all-comment
```

原因：

- 评论区接口请求量更重。
- 更像非正常浏览行为。
- 容易触发平台风控。
- 店名补全收益不稳定，不适合作为主流程依赖。

## 搜索结果阶段

搜索 `辣子鸡` 或 `武商梦时代 川菜` 时，搜索结果候选主要会提取：

- `note_id`：笔记 ID。
- `xsec_token`：打开详情所需 token。
- `title`：搜索卡片标题。
- `author`：作者昵称。
- `liked_count`：点赞数。
- `collected_count`：收藏数。
- `comment_count`：评论数。
- `cover`：封面图。
- `safe_url`：不带敏感 token 的笔记链接。

这些字段来自搜索结果卡片，不需要进入评论区。

## 帖子详情阶段

点开某一篇帖子后，当前代码从 `note_card` 中提取：

- `note_id`
- `title`
- `author`
- `desc`
- `image_urls`
- `liked_count`
- `collected_count`
- `comment_count`
- `safe_url`
- `detail_url`

其中：

- `desc` 最多保留前 2000 字。
- `image_urls` 最多保留 6 张图。
- `safe_url` 用于展示和溯源。
- `detail_url` 仅用于内部详情请求，不应展示给用户。

## 店名识别来源

近期只从以下文本中识别店名：

- 标题。
- 正文。
- 显式字段，例如 `店名：xxx`、`地址：xxx`、`打卡店：xxx`。

不再默认从评论区识别店名。

如果标题和正文无法识别店名：

- 该笔记不进入实时推荐。
- 可写入本地失败缓存或待处理列表。
- 主流程用高德候选补足 3 张卡片。

## 高德补全

从小红书识别到店名后，必须再用高德反查：

- 标准店名。
- 详细地址。
- 楼层/铺位。
- 经纬度。
- 高德评分。
- 人均。
- 高德图片。
- POI ID。

只有高德匹配成功后，小红书笔记才可形成可用餐厅卡片。

## 风控策略

近期默认策略：

- 小红书详情页每次最多打开 4 条。
- 代码硬限制最多 6 条。
- 评论区查询默认关闭。
- 图片只使用帖子详情返回的图片 URL，不额外批量下载。
- 失败笔记应缓存，避免重复请求。

相关配置：

```env
AIONE_XHS_SEARCH_CANDIDATE_LIMIT=6
AIONE_XHS_DETAIL_LIMIT=3
AIONE_XHS_DINING_DETAIL_LIMIT=4
AIONE_XHS_COMMENT_STORE_LOOKUP=0
```

