import os
import time
import json
from datetime import datetime

# 尝试导入 pymilvus 以进行生产级开发
try:
    from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection
    HAS_PYMILVUS = True
except ImportError:
    HAS_PYMILVUS = False

class MilvusManager:
    def __init__(self):
        self.host = os.getenv("MILVUS_HOST", "127.0.0.1")
        self.port = os.getenv("MILVUS_PORT", "19530")
        self.is_mock = False
        
        # 降级时的本地内存/文件存储模拟
        self.mock_footprints = []
        self.mock_chats = []
        self.mock_file_footprint = "logs/mock_milvus_footprints.json"
        self.mock_file_chats = "logs/mock_milvus_chats.json"
        
        self._init_connection()

    def _init_connection(self):
        """
        初始化连接。如果连接失败或缺少包，则自动启用 Mock 本地模拟器。
        """
        if not HAS_PYMILVUS:
            print("【Milvus 警告】未安装 pymilvus 模块，自动启用本地模拟降级。")
            self._load_mock_data()
            return

        try:
            # 尝试连接 Milvus (设置 3 秒超时限制，防死锁)
            connections.connect(alias="default", host=self.host, port=self.port, timeout=3.0)
            self._create_collections_if_not_exist()
            print(f"【Milvus 成功】已成功建立与 {self.host}:{self.port} 的 Milvus 数据库连接。")
        except Exception as e:
            print(f"【Milvus 降级】连接 Milvus 失败 ({e})，正在自动无缝降级为本地内存与文件模拟模式。")
            self._load_mock_data()

    def _load_mock_data(self):
        """从本地 JSON 文件中恢复数据（模拟数据库持久化）"""
        self.is_mock = True
        
        # 恢复足迹数据
        if os.path.exists(self.mock_file_footprint):
            try:
                with open(self.mock_file_footprint, "r", encoding="utf-8") as f:
                    self.mock_footprints = json.load(f)
            except Exception:
                self.mock_footprints = []
                
        # 恢复对话数据
        if os.path.exists(self.mock_file_chats):
            try:
                with open(self.mock_file_chats, "r", encoding="utf-8") as f:
                    self.mock_chats = json.load(f)
            except Exception:
                self.mock_chats = []

    def _save_mock_data(self):
        """将 Mock 内存数据持久化至本地 JSON"""
        os.makedirs("logs", exist_ok=True)
        with open(self.mock_file_footprint, "w", encoding="utf-8") as f:
            json.dump(self.mock_footprints, f, ensure_ascii=False, indent=2)
        with open(self.mock_file_chats, "w", encoding="utf-8") as f:
            json.dump(self.mock_chats, f, ensure_ascii=False, indent=2)

    def _create_collections_if_not_exist(self):
        """在 Milvus 中创建 Collections"""
        # 1. 建立吃货足迹 Collection
        footprint_fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="item_name", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="item_type", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="timestamp", dtype=DataType.INT64),
            FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=2048),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1536)
        ]
        fp_schema = CollectionSchema(footprint_fields, "SuperFoodie 吃货就餐足迹历史")
        
        if not utility.has_collection("foodie_footprint"):
            self.fp_col = Collection(name="foodie_footprint", schema=fp_schema)
            # 为向量创建 IVF_FLAT 索引进行搜索加速
            index_params = {
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
            self.fp_col.create_index("embedding", index_params)
        else:
            self.fp_col = Collection("foodie_footprint")
            
        # 2. 建立对话历史 Collection
        chat_fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="session_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="role", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4096),
            FieldSchema(name="timestamp", dtype=DataType.INT64),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1536)
        ]
        chat_schema = CollectionSchema(chat_fields, "SuperFoodie 智能体对话历史")
        
        if not utility.has_collection("chat_history"):
            self.chat_col = Collection(name="chat_history", schema=chat_schema)
            index_params = {
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 128}
            }
            self.chat_col.create_index("embedding", index_params)
        else:
            self.chat_col = Collection("chat_history")

        # 加载 Collection 到内存
        self.fp_col.load()
        self.chat_col.load()

    # --- 足迹 (Footprint) 数据操作接口 ---

    def insert_footprint(self, user_id: str, item_name: str, item_type: str, metadata: dict = None, embedding: list[float] = None):
        """插入就餐足迹记录"""
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        ts = int(time.time())
        emb = embedding or [0.0] * 1536  # 无嵌入则使用全 0 向量填充
        
        if self.is_mock:
            entry = {
                "id": len(self.mock_footprints) + 1,
                "user_id": user_id,
                "item_name": item_name,
                "item_type": item_type,
                "timestamp": ts,
                "metadata": meta_str,
                "embedding": emb
            }
            self.mock_footprints.append(entry)
            self._save_mock_data()
        else:
            data = [[user_id], [item_name], [item_type], [ts], [meta_str], [emb]]
            self.fp_col.insert(data)
            self.fp_col.flush()

    def get_recent_footprints(self, user_id: str, days: int = 7) -> list[str]:
        """
        获取用户最近几天食用的菜品/餐厅名列表（用于去重）
        """
        cutoff_time = int(time.time()) - (days * 24 * 3600)
        
        if self.is_mock:
            # 过滤符合 user_id 且 timestamp > 截止时间的记录
            recent_items = [
                fp["item_name"] for fp in self.mock_footprints
                if fp["user_id"] == user_id and fp["timestamp"] >= cutoff_time
            ]
            return list(set(recent_items))
        else:
            # 使用 Milvus 的 Scalar 关系检索
            expr = f"user_id == '{user_id}' and timestamp >= {cutoff_time}"
            res = self.fp_col.query(expr=expr, output_fields=["item_name"])
            recent_items = [item["item_name"] for item in res]
            return list(set(recent_items))

    # --- 对话历史 (Chat History) 数据操作接口 ---

    def insert_chat(self, session_id: str, role: str, content: str, embedding: list[float] = None):
        """记录单条对话历史"""
        ts = int(time.time())
        emb = embedding or [0.0] * 1536
        
        if self.is_mock:
            entry = {
                "id": len(self.mock_chats) + 1,
                "session_id": session_id,
                "role": role,
                "content": content,
                "timestamp": ts,
                "embedding": emb
            }
            self.mock_chats.append(entry)
            self._save_mock_data()
        else:
            data = [[session_id], [role], [content], [ts], [emb]]
            self.chat_col.insert(data)
            self.chat_col.flush()

    def get_chat_history(self, session_id: str) -> list[dict]:
        """获取某会话的所有对话历史记录"""
        if self.is_mock:
            history = [
                {"role": c["role"], "content": c["content"], "timestamp": c["timestamp"]}
                for c in self.mock_chats if c["session_id"] == session_id
            ]
            # 按照时间戳排序
            history.sort(key=lambda x: x["timestamp"])
            return history
        else:
            expr = f"session_id == '{session_id}'"
            res = self.chat_col.query(expr=expr, output_fields=["role", "content", "timestamp"])
            res.sort(key=lambda x: x["timestamp"])
            return [{"role": item["role"], "content": item["content"], "timestamp": item["timestamp"]} for item in res]
