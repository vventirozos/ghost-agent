import json
import asyncio
import logging
from typing import List, Dict, Any, Optional
import httpx
from ..utils.logging import Icons, pretty_log
from ..utils.helpers import get_utc_timestamp

logger = logging.getLogger("GhostAgent")

class LLMClient:
    def __init__(self, upstream_url: str):
        self.upstream_url = upstream_url
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        # Explicitly disable proxy for LLM connections to avoid routing through Tor
        # Using a custom transport to ensure proxy=None is respected and handle low-level issues
        self.http_client = httpx.AsyncClient(
            base_url=upstream_url, 
            timeout=600.0, 
            limits=limits,
            proxy=None,
            follow_redirects=True,
            http2=False
        )

    async def close(self):
        await self.http_client.aclose()

    async def chat_completion(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends a chat completion request to the upstream LLM with retry logic.
        """
        for attempt in range(3):
            try:
                resp = await self.http_client.post("/v1/chat/completions", json=payload)
                resp.raise_for_status()
                return resp.json()
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ConnectError) as e:
                if attempt < 2:
                    wait_time = 2 ** attempt
                    pretty_log("Upstream Retry", f"Connection issue: {type(e).__name__}. Retrying in {wait_time}s...", icon=Icons.RETRY)
                    await asyncio.sleep(wait_time)
                else:
                    pretty_log("Upstream Failed", f"Failed after 3 attempts: {str(e)}", level="ERROR", icon=Icons.FAIL)
                    raise
            except httpx.HTTPStatusError as e:
                pretty_log("Upstream Error", f"HTTP {e.response.status_code}: {e.response.text}", level="ERROR", icon=Icons.FAIL)
                raise
            except Exception as e:
                pretty_log("Upstream Fatal", str(e), level="ERROR", icon=Icons.FAIL)
                raise

    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Fetches embeddings from the upstream LLM with retry logic.
        """
        payload = {"input": texts, "model": "default"}
        for attempt in range(3):
            try:
                resp = await self.http_client.post("/v1/embeddings", json=payload)
                resp.raise_for_status()
                data = resp.json()
                # Return the embeddings in the order they were requested
                return [item["embedding"] for item in data["data"]]
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ConnectError) as e:
                if attempt < 2:
                    wait_time = 2 ** attempt
                    await asyncio.sleep(wait_time)
                else:
                    pretty_log("Embedding Failed", f"Upstream error: {str(e)}", level="ERROR", icon=Icons.FAIL)
                    raise
            except Exception as e:
                pretty_log("Embedding Fatal", str(e), level="ERROR", icon=Icons.FAIL)
                raise

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