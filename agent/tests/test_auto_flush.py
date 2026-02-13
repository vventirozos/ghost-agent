import asyncio
import datetime
import sys
import gc
from unittest.mock import MagicMock, AsyncMock, patch

# Add src to path
sys.path.append("src")

from ghost_agent.core.agent import GhostAgent, GhostContext
from ghost_agent.core.llm import LLMClient

async def test_auto_flush_logic():
    print("--- Testing Auto-RAM Flush Logic ---")
    
    # 1. Setup Mock Context
    mock_args = MagicMock()
    mock_args.smart_memory = 0.0
    ctx = MagicMock(spec=GhostContext)
    ctx.args = mock_args
    ctx.scratchpad = MagicMock()
    ctx.cached_sandbox_state = "Found 100 files..."
    
    # Mock LLM Client
    mock_llm = AsyncMock(spec=LLMClient)
    ctx.llm_client = mock_llm
    
    # Initialize Agent
    agent = GhostAgent(ctx)
    
    # 2. Simulate Active State
    print(f"[State] Sandbox Cache: {ctx.cached_sandbox_state}")
    
    # 3. Call clear_session (The Action to be tested)
    print(">>> Triggering clear_session()...")
    agent.clear_session()
    
    # 4. Verify Side Effects
    
    # A. Check Scratchpad clear
    ctx.scratchpad.clear.assert_called_once()
    print("[PASS] Scratchpad cleared.")
    
    # B. Check Sandbox Cache wipe
    if ctx.cached_sandbox_state is None:
        print("[PASS] Sandbox cache verified None.")
    else:
        print(f"[FAIL] Sandbox cache is {ctx.cached_sandbox_state}")
        
    # C. Check LLM Context Reset Trigger
    # Note: It's an asyncio.create_task, so we might need a tiny sleep or check if it was called
    # Since we mocked the client, checking the method call on the mock is enough.
    # However, create_task schedules it.
    await asyncio.sleep(0.1) 
    mock_llm.reset_context.assert_called_once()
    print("[PASS] LLM reset_context triggered.")
    
    print("--- Test Complete ---")

if __name__ == "__main__":
    asyncio.run(test_auto_flush_logic())
