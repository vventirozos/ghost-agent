import json
import datetime
from typing import List, Dict, Any, Optional
import httpx
from ..utils.logging import Icons, pretty_log
from ..utils.helpers import get_utc_timestamp

class LLMClient:
    def __init__(self, upstream_url: str):
        self.upstream_url = upstream_url
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        # Explicitly disable proxy for LLM connections to avoid routing through Tor
        self.http_client = httpx.AsyncClient(
            base_url=upstream_url, 
            timeout=600.0, 
            limits=limits,
            proxy=None
        )

    async def close(self):
        await self.http_client.aclose()

    async def chat_completion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self.http_client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def stream_openai(self, model: str, content: str, created_time: int, req_id: str):
        chunk_id = f"chatcmpl-{req_id}"
        start_chunk = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created_time,
            "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
        }
        yield f"data: {json.dumps(start_chunk)}\n\n".encode('utf-8')

        content_chunk = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created_time,
            "model": model, "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
        }
        yield f"data: {json.dumps(content_chunk)}\n\n".encode('utf-8')

        stop_chunk = {
            "id": chunk_id, "object": "chat.completion.chunk", "created": created_time,
            "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(stop_chunk)}\n\n".encode('utf-8')
        yield b"data: [DONE]\n\n"
