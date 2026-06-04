import os
import re

# 尝试导入 networkx 以提供生产级的高性能图数据操作
try:
    import networkx as nx
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

class GraphRAGRetriever:
    def __init__(self, vault_path: str):
        self.vault_path = vault_path
        self.edges = []
        self.nodes = set()
        
        # 兼容性图结构初始化
        if HAS_NETWORKX:
            self.graph = nx.DiGraph()
        else:
            self.graph_dict = {}  # 邻接表表示法: {node: [targets]}
            
        self.build_graph()

    def build_graph(self):
        """
        扫描 obsidian_vault 下的 Markdown 文件并解析 [[双链]] 引用。
        有向边方向: 节点 -> 被引用节点 (如: 感冒 -> 忌温热性)
        """
        if not os.path.exists(self.vault_path):
            return

        for root, dirs, files in os.walk(self.vault_path):
            for file in files:
                if file.endswith(".md"):
                    node_name = file.replace(".md", "").strip()
                    file_path = os.path.join(root, file)
                    
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()
                    except Exception:
                        continue
                    
                    # 匹配双括号链接 [[link_name]]
                    links = re.findall(r"\[\[([^\]]+)\]\]", content)
                    self.nodes.add(node_name)
                    
                    for link in links:
                        link = link.strip()
                        self.nodes.add(link)
                        self.edges.append((node_name, link))
                        
                        # 写入图结构
                        if HAS_NETWORKX:
                            self.graph.add_edge(node_name, link)
                        else:
                            if node_name not in self.graph_dict:
                                self.graph_dict[node_name] = []
                            self.graph_dict[node_name].append(link)

    def _dfs_reach(self, start_node: str) -> set[str]:
        """
        纯 Python 深度优先搜索，获取 start_node 在有向图中能够到达的所有节点集合。
        """
        visited = set()
        stack = [start_node]
        
        while stack:
            curr = stack.pop()
            if curr not in visited:
                visited.add(curr)
                # 获取邻居
                neighbors = []
                if HAS_NETWORKX:
                    if self.graph.has_node(curr):
                        neighbors = list(self.graph.successors(curr))
                else:
                    neighbors = self.graph_dict.get(curr, [])
                
                for n in neighbors:
                    if n not in visited:
                        stack.append(n)
        return visited

    def query_diet_safety(self, current_disease: str, target_food: str) -> dict:
        """
        查询某种身体状况（疾病）与某种食物之间是否存在饮食冲突。
        
        原理：
        1. 从疾病节点（例如 "感冒"）出发，获取其所有可以通过有向边到达的属性/规则集合 (Disease-Reach)
        2. 从食物节点（例如 "牛肉"）出发，获取其所有可以通过有向边到达的属性集合 (Food-Reach)
        3. 如果两个集合的交集（排除它们自身）不为空，则说明存在冲突属性！
           例如: 感冒 -> 忌温热性 -> 温热性 (交集点) <- 牛肉
        """
        current_disease = current_disease.strip()
        target_food = target_food.strip()

        # 如果节点都不在图里，默认认为是安全的
        if current_disease not in self.nodes or target_food not in self.nodes:
            return {
                "safety": True,
                "reason": f"无图谱记录表明 '{target_food}' 对 '{current_disease}' 有不良反应。",
                "conflict_node": None,
                "path": []
            }

        # 1. 疾病可达集合
        disease_reach = self._dfs_reach(current_disease)
        # 2. 食物可达集合
        food_reach = self._dfs_reach(target_food)

        # 3. 计算冲突交集
        intersection = disease_reach.intersection(food_reach)
        
        # 移出节点自身（防自环干扰）
        intersection.discard(current_disease)
        intersection.discard(target_food)

        if intersection:
            conflict_node = list(intersection)[0]  # 取第一个冲突属性点（如 "温热性"）
            
            # 生成便于人类阅读的拦截原因
            reason = f"【拦截警告】检测到 '{target_food}' 具有属性 [[{conflict_node}]]，而您的身体状况 '{current_disease}' [[忌{conflict_node}]]。"
            
            # 组装展示的冲突路径
            path = [current_disease, f"忌{conflict_node}", conflict_node, f"源自: {target_food}"]
            
            return {
                "safety": False,
                "reason": reason,
                "conflict_node": conflict_node,
                "path": path
            }

        return {
            "safety": True,
            "reason": f"图谱分析：在当前状况 '{current_disease}' 下，食用 '{target_food}' 是安全的。",
            "conflict_node": None,
            "path": []
        }
