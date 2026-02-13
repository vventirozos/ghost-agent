import asyncio
import inspect
from ghost_agent.tools.registry import get_available_tools

class MockContext:
    def __init__(self):
        self.tor_proxy = None
        self.sandbox_dir = None
        self.memory_system = None
        self.profile_memory = None
        self.scheduler = None
        self.skill_memory = None
        self.llm_client = None
        # args
        class Args:
            anonymous = False
        self.args = Args()

async def test_replan_is_awaitable():
    context = MockContext()
    tools = get_available_tools(context)
    
    replan_func = tools.get("replan")
    if not replan_func:
        print("FAILURE: replan tool not found")
        exit(1)
        
    # Call the tool
    result = replan_func(reason="test")
    
    # Check if result is awaitable
    if inspect.isawaitable(result):
        res_text = await result
        print(f"SUCCESS: replan returned awaitable. Result: {res_text}")
    else:
        print(f"FAILURE: replan returned {type(result)}, expected awaitable")
        exit(1)

if __name__ == "__main__":
    asyncio.run(test_replan_is_awaitable())
