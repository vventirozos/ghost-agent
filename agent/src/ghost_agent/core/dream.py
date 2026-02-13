import json
import logging
import asyncio
from typing import List, Dict, Any
from .prompts import SYSTEM_PROMPT
from ..utils.logging import Icons, pretty_log

logger = logging.getLogger("GhostAgent")

class Dreamer:
    """
    Active Memory Consolidation System.
    "Dreams" about recent memories to synthesize them into higher-order facts.
    """
    def __init__(self, agent_context):
        self.context = agent_context
        self.memory = agent_context.memory_system

    async def dream(self, model_name: str = "ghost-agent"):
        """
        Main consolidation loop.
        """
        if not self.memory or not self.memory.collection:
            return "Memory system not available."

        pretty_log("Dream Mode", "Entering REM cycle (Consolidating Memory)...", icon="🌙")
        
        # 1. Fetch Volatile Memories (Type="auto")
        # We fetch a large batch to find clusters
        try:
            results = self.memory.collection.get(
                where={"type": "auto"},
                limit=100,
                include=["documents", "metadatas", "embeddings"]
            )
        except Exception as e:
            return f"Dream error: {e}"

        ids = results['ids']
        documents = results['documents']
        metadatas = results['metadatas']
        embeddings = results['embeddings']
        
        if len(documents) < 3:
            return "Not enough entropy to dream. (Need > 3 auto-memories)"

        # 2. Simple Clustering (Distance Based)
        # Since we don't have numpy/sklearn guaranteed in the environment,
        # we'll use a greedy clustering approach based on the existing ChromaDB structure
        # or just simple text overlap if embeddings aren't easily math-able here.
        
        # Actually, let's use the LLM to cluster for us to be dependency-free.
        # We'll batch them and ask for "Themes".
        
        mem_list = [f"ID:{i} | {doc}" for i, doc in zip(ids, documents)]
        mem_block = "\n".join(mem_list[:50]) # Dream about top 50

        pretty_log("Dream Mode", f"Analyzing {len(ids)} fragments...", icon="🧠")

        prompt = f"""
### MEMORY CONSOLIDATION TASK
Below is a list of raw, fragmented memories.
Your job is to identify **overlapping or related facts** and MERGE them into single, high-density facts.

RAW MEMORIES:
{mem_block}

INSTRUCTIONS:
1. Identify groups of 2+ memories that talk about the same specific topic.
2. SYNTHESIZE them into a single, comprehensive statement.
3. Mark the IDs of the original memories for DELETION.

OUTPUT FORMAT (JSON ONLY):
{{
  "consolidations": [
    {{
      "synthesis": "The user is working on a Python-based Ghost Agent in /src/ghost_agent, focusing on memory optimization.",
      "merged_ids": ["ID:...", "ID:..."]
    }}
  ]
}}
If no obvious merges exist, return empty list.
"""
        
        try:
            payload = {
                "model": model_name,
                "messages": [{"role": "system", "content": "You are a Memory Optimizer."}, {"role": "user", "content": prompt}],
                "temperature": 0.0,
                "response_format": {"type": "json_object"}
            }
            data = await self.context.llm_client.chat_completion(payload)
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
            
            consolidations = result.get("consolidations", [])
            
            if not consolidations:
                return "Dream cycle complete. No patterns found."

            ops_log = []
            
            for item in consolidations:
                synthesis = item["synthesis"]
                merged_ids = item["merged_ids"]
                
                # Check if we are actually reducing count
                stripped_ids = [mid.replace("ID:", "").strip() for mid in merged_ids]
                
                if len(stripped_ids) > 1:
                    # ADD new fact
                    self.memory.add(synthesis, {"type": "consolidated_fact", "timestamp": "DREAM_CYCLE"})
                    
                    # DELETE old fragments
                    self.memory.collection.delete(ids=stripped_ids)
                    
                    ops_log.append(f"Merged {len(stripped_ids)} items -> '{synthesis[:50]}...'")
                    pretty_log("Dream Merge", f"Consolidated {len(stripped_ids)} into 1: {synthesis[:40]}...", icon="✨")

            summary = "\n".join(ops_log)
            pretty_log("Dream Wake", f"Consolidation Complete:\n{summary}", icon="☀️")
            return f"Dream Complete. Operations:\n{summary}"

        except Exception as e:
            return f"Dream failed: {e}"
