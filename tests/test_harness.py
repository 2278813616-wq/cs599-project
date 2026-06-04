# tests/test_harness.py

from src.harness.quality_check import check_variance, check_resume_confidence
from src.harness.audit_log import AuditLogger
from src.harness.output_validator import OutputValidator
import os
import json
from datetime import datetime
from pathlib import Path


def test_check_variance():
    """测试方差检查"""
    # 稳定评分
    result = check_variance([75, 76, 75])
    assert result["variance"] < 5
    assert result["confidence"] == "high"
    
    # 波动评分
    result = check_variance([50, 90, 70])
    assert result["variance"] > 20
    assert result["confidence"] == "low"


def test_check_resume_confidence():
    """测试简历置信度"""
    # 完整简历
    result = check_resume_confidence({
        "name": "张三",
        "skills": ["Python", "Java"],
        "experience": [{"company": "ABC"}],
        "education": [{"school": "XX"}]
    })
    assert result["confidence"] >= 0.7
    assert result["passed"] == True
    
    # 不完整简历
    result = check_resume_confidence({
        "name": "李四",
        "skills": [],
        "experience": []
    })
    assert result["confidence"] < 0.7
    assert result["passed"] == False


def test_audit_logger():
    """测试审计日志的持久化与读取"""
    session_id = f"test-session-{datetime.now().timestamp()}"
    logger = AuditLogger(session_id)
    
    # 记录事件
    test_data = {"key": "value"}
    logger.log("test_event", test_data)
    
    # 验证内存/即时逻辑
    trail = AuditLogger.get_trail(session_id)
    assert len(trail) > 0
    assert trail[-1]["event"] == "test_event"
    assert trail[-1]["data"] == test_data
    
    # 验证文件物理存在
    log_file = Path("logs/audit.jsonl")
    assert log_file.exists()
    
    # 验证文件内容包含最后一条记录
    with open(log_file, "r", encoding="utf-8") as f:
        last_line = f.readlines()[-1]
        entry = json.loads(last_line)
        assert entry["session_id"] == session_id


def test_output_validator():
    """测试输出验证器"""
    # 假设 resume_schema.yaml 存在
    validator = OutputValidator("resume_schema.yaml")
    
    # 有效数据
    valid_data = {
        "name": "张三",
        "skills": ["Python"],
        "experience": [{"company": "A", "role": "B"}]
    }
    passed, errors = validator.validate(valid_data)
    assert passed == True
    assert len(errors) == 0
    
    # 无效数据
    invalid_data = {"name": "张三"} # 缺失 skills 和 experience
    passed, errors = validator.validate(invalid_data)
    assert passed == False
    assert len(errors) > 0
