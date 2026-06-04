import yaml
import os

class OutputValidator:
    def __init__(self, schema_path: str):
        self.schema_path = schema_path
        self.schema = self._load_schema()

    def _load_schema(self) -> dict:
        if not os.path.exists(self.schema_path):
            raise FileNotFoundError(f"Schema file not found at {self.schema_path}")
        with open(self.schema_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def validate(self, data: dict) -> tuple[bool, list[str]]:
        """
        验证输入数据是否符合 YAML 规范定义的类型和必填键。
        返回: (是否通过: bool, 错误列表: list[str])
        """
        errors = []
        if not isinstance(data, dict):
            return False, ["Input data must be a dictionary"]

        required_fields = self.schema.get("required", [])
        properties = self.schema.get("properties", {})

        # 1. 检查必填项
        for field in required_fields:
            if field not in data:
                errors.append(f"Missing required field: '{field}'")

        # 2. 检查字段类型和子属性
        for key, val in data.items():
            if key not in properties:
                continue  # 忽略未在 schema 里声明的多余字段，或者也可以报错，目前按忽略处理
            
            field_schema = properties[key]
            expected_type = field_schema.get("type")
            
            if expected_type == "string":
                if not isinstance(val, str):
                    errors.append(f"Field '{key}' must be a string, got {type(val).__name__}")
            elif expected_type == "array":
                if not isinstance(val, list):
                    errors.append(f"Field '{key}' must be an array (list), got {type(val).__name__}")
                else:
                    # 检查 array 内的 items 类型
                    item_schema = field_schema.get("items", {})
                    item_type = item_schema.get("type")
                    for idx, item in enumerate(val):
                        if item_type == "string" and not isinstance(item, str):
                            errors.append(f"Field '{key}[{idx}]' must be a string, got {type(item).__name__}")
                        elif item_type == "object" and not isinstance(item, dict):
                            errors.append(f"Field '{key}[{idx}]' must be an object (dict), got {type(item).__name__}")
                        elif item_type == "object" and isinstance(item, dict):
                            # 递归校验子 object
                            sub_required = item_schema.get("required", [])
                            for req in sub_required:
                                if req not in item:
                                    errors.append(f"Missing field '{req}' in '{key}[{idx}]'")
            elif expected_type == "object":
                if not isinstance(val, dict):
                    errors.append(f"Field '{key}' must be an object (dict), got {type(val).__name__}")
                else:
                    # 递归校验子 object
                    sub_required = field_schema.get("required", [])
                    for req in sub_required:
                        if req not in val:
                            errors.append(f"Missing required field '{req}' in '{key}'")

        return len(errors) == 0, errors
