from typing import Dict, Any, Optional

class Scratchpad:
    def __init__(self):
        self._data: Dict[str, Any] = {}

    def set(self, key: str, value: Any):
        self._data[key] = value
        return f"Stored: {key} = {value}"

    def get(self, key: str) -> Optional[Any]:
        return self._data.get(key, None)

    def list_all(self) -> str:
        if not self._data:
            return "Scratchpad is empty."
        return "\n".join([f"{k}: {v}" for k, v in self._data.items()])