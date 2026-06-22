# -*- coding: utf-8 -*-
import os
import sys
import time
import json
import httpx
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

print("==================================================")
print("     SuperFoodie 吃货核心 API 与组件真实性诊断")
print("==================================================")
print("提示：本测试脚本将跳过任何本地 Mock 降级逻辑，直接向真实服务发起连接。")

# 1. 诊断大模型 API
print("\n--- [1] 诊断大模型 API (DashScope / DeepSeek) ---")
llm_key = os.getenv("LLM_API_KEY")
llm_base = os.getenv("LLM_API_BASE", "https://api.bianxie.ai/v1")
llm_model = os.getenv("LLM_MODEL", "deepseek-chat")

if not llm_key or llm_key.strip() == "your_llm_api_key_here":
    print("【✘ 失败】未在 .env 中配置有效的 LLM_API_KEY。")
else:
    print(f"API Base: {llm_base}")
    print(f"Model: {llm_model}")
    try:
        resp = httpx.post(
            f"{llm_base}/chat/completions",
            headers={"Authorization": f"Bearer {llm_key}"},
            json={
                "model": llm_model,
                "messages": [{"role": "user", "content": "你好，这是一次大模型连接测试。请简短回答'连接成功'。"}],
                "temperature": 0.5
            },
            timeout=8.0
        )
        if resp.status_code == 200:
            print("【✔ 成功】大模型真实调用返回：")
            print(resp.json()["choices"][0]["message"]["content"])
        else:
            print(f"【✘ 失败】接口返回错误状态码: {resp.status_code}")
            print(f"响应内容: {resp.text}")
    except Exception as e:
        print(f"【✘ 失败】连接大模型接口抛出异常: {e}")


# 2. 诊断 Milvus 数据库真实性 (包含建表、测试向量写入和真实数据检索)
print("\n--- [2] 诊断 Milvus 数据库真实物理连接与存取 ---")
milvus_host = os.getenv("MILVUS_HOST", "127.0.0.1")
milvus_port = os.getenv("MILVUS_PORT", "19530")
try:
    from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection
    print(f"正在尝试连接 Milvus: {milvus_host}:{milvus_port} ...")
    connections.connect(alias="default_test", host=milvus_host, port=milvus_port, timeout=5.0)
    print("【✔ 成功】Milvus 数据库 TCP 连接已建立。")
    
    # 检查核心表是否存在
    has_fp = utility.has_collection("foodie_footprint", using="default_test")
    has_chat = utility.has_collection("chat_history", using="default_test")
    print(f"数据库中 'foodie_footprint' 表状态: {'【✔ 已建表】' if has_fp else '【✘ 未建表】'}")
    print(f"数据库中 'chat_history' 表状态: {'【✔ 已建表】' if has_chat else '【✘ 未建表】'}")
    
    # 进行一次真实读写测试
    if has_fp:
        col = Collection("foodie_footprint", using="default_test")
        test_uid = f"test_diagnose_{int(time.time())}"
        test_item = "测试验证菜品"
        test_type = "recipe"
        ts = int(time.time())
        meta_str = json.dumps({"test_key": "test_val"})
        emb = [0.0] * 1536
        
        print("正在向 'foodie_footprint' 真实插入测试数据...")
        data = [[test_uid], [test_item], [test_type], [ts], [meta_str], [emb]]
        col.insert(data)
        col.flush(using="default_test")
        print("【✔ 成功】测试数据已成功 Flush 写入 Milvus。")
        
        # 真实读取校验
        col.load()
        expr = f"user_id == '{test_uid}'"
        res = col.query(expr=expr, output_fields=["id", "user_id", "item_name", "timestamp"])
        if res:
            print("【✔ 成功】已成功从 Milvus 库中 Query 检索到刚才写入的测试数据！")
            print(f"检索结果: {res}")
            # 根据主键安全删除 (Milvus 仅支持通过主键进行 delete 过滤)
            pks = [item.get("id") for item in res if "id" in item or item.get("id") is not None]
            if pks:
                col.delete(expr=f"id in {pks}")
                print("测试数据已从 Milvus 中安全清理。")
            else:
                print("【警告】未提取到测试数据的主键，跳过清理阶段。")
        else:
            print("【✘ 失败】无法从 Milvus 中检索到刚写入的测试记录，请检查 Milvus 索引或存储状态。")
            
    connections.disconnect("default_test")
except ImportError:
    print("【✘ 失败】未安装 pymilvus SDK 依赖包，请在环境中运行：pip install pymilvus")
except Exception as e:
    print(f"【✘ 失败】Milvus 数据库读写或连接异常: {e}")


# 3. 诊断高德地图 API
print("\n--- [3] 诊断高德地图 API 定位与反查 ---")
gaode_key = os.getenv("GAODE_API_KEY")
if not gaode_key or gaode_key.strip() == "your_gaode_api_key_here":
    print("【✘ 失败】未在 .env 中配置有效的 GAODE_API_KEY。")
else:
    print("正在测试高德地图地理编码解析地址 '天安门'...")
    try:
        url = f"https://restapi.amap.com/v3/geocode/geo"
        resp = httpx.get(url, params={"address": "天安门", "key": gaode_key}, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "1" and data.get("geocodes"):
                location = data["geocodes"][0]["location"]
                print(f"【✔ 成功】高德地图 API 解析成功，'天安门' 坐标为: {location}")
            else:
                print(f"【✘ 失败】高德地图 API 返回失败状态: {data}")
        else:
            print(f"【✘ 失败】高德地图接口返回状态码: {resp.status_code}")
    except Exception as e:
        print(f"【✘ 失败】高德地图 API 网络请求异常: {e}")


# 4. 诊断 Tavily 搜索 MCP 后端 Key
print("\n--- [4] 诊断 Tavily 搜索服务 (TAVILY_API_KEY) ---")
tavily_key = os.getenv("TAVILY_API_KEY")
if not tavily_key or tavily_key.strip() == "your_tavily_api_key_here":
    print("【! 未配置】TAVILY_API_KEY 缺失，联网搜索能力目前受限。")
else:
    print("正在向 Tavily 真实发起搜索请求 '北京烤鸭做法'...")
    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": tavily_key, "query": "北京烤鸭 做法", "max_results": 2},
            timeout=8.0
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            print("【✔ 成功】Tavily 搜索返回前 2 条记录：")
            for idx, r in enumerate(results):
                print(f"  {idx+1}. {r.get('title')} ({r.get('url')})")
        else:
            print(f"【✘ 失败】Tavily 返回错误码 {resp.status_code}，详情: {resp.text}")
    except Exception as e:
        print(f"【✘ 失败】Tavily 网络连接异常: {e}")


# 5. 诊断 Firecrawl 网页爬取 (FIRECRAWL_API_KEY)
print("\n--- [5] 诊断 Firecrawl 网页爬取服务 ---")
firecrawl_key = os.getenv("FIRECRAWL_API_KEY")
if not firecrawl_key or firecrawl_key.strip() == "your_firecrawl_api_key_here":
    print("【! 未配置】FIRECRAWL_API_KEY 缺失，无法进行网页爬取与清洗。")
else:
    print("正在向 Firecrawl 真实发起网页爬取请求 'https://example.com'...")
    try:
        resp = httpx.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {firecrawl_key}", "Content-Type": "application/json"},
            json={"url": "https://example.com"},
            timeout=10.0
        )
        if resp.status_code == 200:
            print("【✔ 成功】Firecrawl 网页爬取成功！")
            markdown_snippet = resp.json().get("data", {}).get("markdown", "")[:150]
            print(f"爬取数据片段: {markdown_snippet}...")
        else:
            print(f"【✘ 失败】Firecrawl 返回状态码 {resp.status_code}，详情: {resp.text}")
    except Exception as e:
        print(f"【✘ 失败】Firecrawl 网络连接异常: {e}")


# 6. 诊断 Apify 社交抓取服务 (APIFY_API_TOKEN)
print("\n--- [6] 诊断 Apify 社交抓取服务 (小红书/美团) ---")
apify_token = os.getenv("APIFY_API_TOKEN")
if not apify_token or apify_token.strip() == "your_apify_api_token_here":
    print("【! 未配置】APIFY_API_TOKEN 缺失，小红书评论抓取等社交层功能受限。")
else:
    print("正在向 Apify 真实发起 Token 验证请求...")
    try:
        resp = httpx.get(f"https://api.apify.com/v2/users/me?token={apify_token}", timeout=8.0)
        if resp.status_code == 200:
            username = resp.json().get("data", {}).get("username", "未知")
            print(f"【✔ 成功】Apify 验证成功！账号用户名: {username}")
        else:
            print(f"【✘ 失败】Apify 返回状态码 {resp.status_code}，详情: {resp.text}")
    except Exception as e:
        print(f"【✘ 失败】Apify 网络连接异常: {e}")


# 7. 诊断 Graph-RAG (Obsidian Vault 知识图谱安全校验)
print("\n--- [7] 诊断 Graph-RAG Obsidian 知识图谱构建与检索 ---")
try:
    from src.tools.graph_rag_retriever import GraphRAGRetriever
    vault_path = "obsidian_vault"
    print(f"正在扫描本地 Obsidian Vault: {vault_path} ...")
    retriever = GraphRAGRetriever(vault_path)
    print(f"【✔ 成功】知识图谱构建完成！")
    print(f"  节点总数: {len(retriever.nodes)}")
    print(f"  边总数 (有向关联): {len(retriever.edges)}")
    
    # 模拟真实安全检索
    print("\n正在对 '感冒' + '牛肉' 进行真实 Graph-RAG 安全性可达测试...")
    safety_res = retriever.query_diet_safety("感冒", "牛肉")
    print(f"  安全判定 (safety): {safety_res.get('safety')}")
    print(f"  检测结论 (reason): {safety_res.get('reason')}")
    print(f"  冲突可达路径 (path): {safety_res.get('path')}")
except Exception as e:
    print(f"【✘ 失败】Graph-RAG 构建与检索测试异常: {e}")


# 8. 诊断 PDF 生成与中文字体、图片绘制 (拒绝乱码与无图)
print("\n--- [8] 诊断 PDF 决策书生成与中文字体、图片绘制 ---")
try:
    from src.tools.report_generator import FoodieReportGenerator
    generator = FoodieReportGenerator()
    
    # 构造测试报告数据 (包含中文字符与本地图片路径)
    test_session = "test-session-diagnose-12345"
    test_data = {
        "user_id": "diagnose_user_test",
        "mode": "home_cooking",
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "recommendation_text": "【主厨美味推荐：清蒸鸭肉】\n配料：鲜鸭肉 300g，生姜 5片，小葱 2根，料酒 10ml。\n烹饪火候：水开后放入鸭肉，微火慢蒸 20 分钟以锁住肉汁。\n【提示】本推荐文案中包含中文字符与换行符以验证 PDF 字体和段落排版功能！",
        "graph_rag_path": ["感冒", "忌温热性", "温热性", "源自: 牛肉"],
        "health_explanation": "安全警告测试：由于检测到感冒，系统已拦截热性的牛肉，并自动为您安全平替为凉性的清蒸鸭肉！",
        "navigation_info": None,
        "image_url": "/images/steamed_duck.png" # 系统本地存放的图片，位于 static/images/
    }
    
    os.makedirs("docs/reports", exist_ok=True)
    pdf_output_path = "docs/reports/test_font_image_report.pdf"
    
    print(f"正在生成测试 PDF 报告至: {pdf_output_path} ...")
    final_path = generator.generate_report(test_session, test_data, pdf_output_path)
    
    if final_path.endswith(".pdf") and os.path.exists(final_path):
        print(f"【✔ 成功】PDF 决策报告成功生成！文件大小: {os.path.getsize(final_path)} 字节。")
        print("💡 重要提示：")
        print(f"  请您立即在本地打开： {os.path.abspath(final_path)}")
        print("  检查并确认：")
        print("  1. 所有的汉字(如标题、元数据字段)都能清晰正确地显示，无黑块与乱码。")
        print("  2. 在报告第一页上方能够清晰展示【清蒸鸭肉】的彩色食物插图。")
        print("  3. 核心推荐的换行段落能够正确对齐并换行显示。")
    else:
        print(f"【✘ 失败】未能正确生成 PDF，文件输出可能发生了降级: {final_path}")
except Exception as e:
    print(f"【✘ 失败】PDF 生成测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n==================================================")
print("             诊断与真实性校验测试结束")
print("==================================================")
