def check_variance(scores: list[float]) -> dict:
    """
    方差检查：评估评分结果的波动程度
    """
    if not scores:
        return {"variance": 0.0, "confidence": "low"}
    
    avg = sum(scores) / len(scores)
    variance = sum((s - avg) ** 2 for s in scores) / len(scores)
    return {
        "variance": variance,
        "confidence": "high" if variance < 5 else "low"
    }


def check_resume_confidence(extracted: dict) -> dict:
    """
    置信度检查：评估简历信息提取的完整性
    """
    score = 0.0
    
    # 1. 必填字段存在性检查 (+0.4)
    if all(k in extracted for k in ["name", "skills", "experience"]):
        score += 0.4
        
    # 2. 技能数量检查 (+0.2)
    skills = extracted.get("skills", [])
    if isinstance(skills, list) and len(skills) >= 3:
        score += 0.2
        
    # 3. 工作经历数量检查 (+0.2)
    experience = extracted.get("experience", [])
    if isinstance(experience, list) and len(experience) >= 1:
        score += 0.2
        
    # 4. 教育背景检查 (+0.2)
    education = extracted.get("education")
    if education:
        score += 0.2
        
    return {
        "confidence": round(score, 2),
        "passed": score >= 0.7
    }
