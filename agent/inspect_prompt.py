
import asyncio
import json
from src.ghost_agent.core.prompts import SYSTEM_PROMPT
from src.ghost_agent.tools.registry import TOOL_DEFINITIONS

# Mock Profile
mock_profile = """
## Root:
- location: Athens, Greece
- name: User
"""

async def test_prompt():
    print("--- SYSTEM PROMPT PREVIEW ---")
    prompt = SYSTEM_PROMPT.replace("{{PROFILE}}", mock_profile).replace("{{CURRENT_TIME}}", "2024-05-20 12:00:00")
    print(prompt[:1000] + "\n...[TRUNCATED]...\n" + prompt[-500:])
    
    print("\n--- TOOL DEFINITIONS ---")
    print(json.dumps(TOOL_DEFINITIONS, indent=2))

if __name__ == "__main__":
    asyncio.run(test_prompt())
