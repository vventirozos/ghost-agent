import json
from pathlib import Path
from typing import Any, Dict
from ..utils.logging import pretty_log

class ProfileMemory:
    def __init__(self, path: Path):
        self.file_path = path / "user_profile.json"
        if not self.file_path.exists():
            self.save({"root": {"name": "User"}, "relationships": {}, "interests": {}, "assets": {}})

    def load(self) -> Dict[str, Any]:
        try: 
            return json.loads(self.file_path.read_text())
        except: 
            return {"root": {"name": "User"}, "relationships": {}, "interests": {}, "assets": {}}

    def save(self, data: Dict[str, Any]):
        self.file_path.write_text(json.dumps(data, indent=2))

    def update(self, category: str, key: str, value: Any):
        data = self.load()
        cat = str(category).strip().lower()
        k = str(key).strip().lower()
        v = str(value).strip()

        # --- STRICT MAPPING (Prevents Duplicates) ---
        mapping = {
            "wife": ("relationships", "wife"),
            "husband": ("relationships", "husband"),
            "son": ("relationships", "son"),
            "daughter": ("relationships", "daughter"),
            "car": ("assets", "car"),
            "vehicle": ("assets", "car"),
            "science": ("interests", "science"),
            "interest": ("interests", "general")
        }

        if k in mapping:
            cat, target_key = mapping[k]
        else:
            target_key = k

        # Ensure category exists as a dictionary
        if cat not in data or not isinstance(data[cat], dict):
            data[cat] = {}

        data[cat][target_key] = v
        self.save(data)
        return f"Synchronized: {cat}.{target_key} = {v}"

    def get_context_string(self) -> str:
        data = self.load()
        lines = []
        for key, val in data.items():
            if not val: continue
            label = key.replace("_", " ").capitalize()
            if isinstance(val, dict):
                lines.append(f"## {label}:")
                for sub_k, sub_v in val.items():
                    lines.append(f"- {sub_k}: {sub_v}")
            elif isinstance(val, list):
                lines.append(f"## {label}: " + ", ".join([str(i) for i in val]))
            else:
                lines.append(f"{label}: {val}")
        return "\n".join(lines)