import os
import json
from datetime import datetime

class AuditLogger:
    # 静态字典：用于即时内存查询，避免每次检索频繁读取磁盘文件
    _trails = {}
    LOG_FILE = "logs/audit.jsonl"

    def __init__(self, session_id: str):
        self.session_id = session_id
        if self.session_id not in AuditLogger._trails:
            AuditLogger._trails[self.session_id] = []

    def log(self, event: str, data: dict):
        """
        实时持久化事件记录至 logs/audit.jsonl 并更新内存快照。
        """
        entry = {
            "session_id": self.session_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": event,
            "data": data
        }

        # 1. 更新内存快照
        AuditLogger._trails[self.session_id].append(entry)

        # 2. 追加物理磁盘文件 (JSONL 格式)
        os.makedirs(os.path.dirname(AuditLogger.LOG_FILE), exist_ok=True)
        with open(AuditLogger.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def get_trail(session_id: str) -> list[dict]:
        """
        根据会话 ID 获取对应的全部事件追踪日志。
        优先从内存获取；若内存中缺失（例如服务重启后），则从磁盘物理日志文件中反序列化扫描。
        """
        # 1. 优先内存检索
        if session_id in AuditLogger._trails and AuditLogger._trails[session_id]:
            return AuditLogger._trails[session_id]

        # 2. 磁盘持久化扫描
        trail = []
        if os.path.exists(AuditLogger.LOG_FILE):
            with open(AuditLogger.LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("session_id") == session_id:
                            trail.append(entry)
                    except json.JSONDecodeError:
                        continue
        
        # 写入内存加速后续查询
        AuditLogger._trails[session_id] = trail
        return trail
