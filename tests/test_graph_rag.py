# tests/test_graph_rag.py

from src.tools.graph_rag_retriever import GraphRAGRetriever

def test_graph_rag_flu_beef():
    """测试感冒患者不能吃牛肉的拦截逻辑"""
    retriever = GraphRAGRetriever("obsidian_vault")
    
    # 牛肉是温热性食材，感冒忌温热性，应该被拦截
    result = retriever.query_diet_safety("感冒", "牛肉")
    assert result["safety"] == False
    assert result["conflict_node"] == "温热性"
    assert "温热性" in result["reason"]


def test_graph_rag_flu_duck():
    """测试感冒患者可以吃鸭肉（鸭肉是凉性的，不冲突）"""
    retriever = GraphRAGRetriever("obsidian_vault")
    
    result = retriever.query_diet_safety("感冒", "鸭肉")
    assert result["safety"] == True
    assert result["conflict_node"] is None


def test_graph_rag_gout_beer():
    """测试痛风患者不能喝啤酒的拦截逻辑"""
    retriever = GraphRAGRetriever("obsidian_vault")
    
    # 啤酒是高嘌呤，痛风忌高嘌呤，应该被拦截
    result = retriever.query_diet_safety("痛风", "啤酒")
    assert result["safety"] == False
    assert result["conflict_node"] == "高嘌呤"


def test_graph_rag_gout_seafood():
    """测试痛风患者不能吃海鲜的拦截逻辑"""
    retriever = GraphRAGRetriever("obsidian_vault")
    
    result = retriever.query_diet_safety("痛风", "海鲜")
    assert result["safety"] == False
    assert result["conflict_node"] == "高嘌呤"


def test_graph_rag_unknown_nodes():
    """测试未知节点（不在图谱中）的默认安全放行逻辑"""
    retriever = GraphRAGRetriever("obsidian_vault")
    
    result = retriever.query_diet_safety("健康人", "苹果")
    assert result["safety"] == True
    assert result["conflict_node"] is None
