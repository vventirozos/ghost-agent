
import asyncio
from pathlib import Path
from unittest.mock import MagicMock
from src.ghost_agent.tools.system import tool_system_utility

# Mock ProfileMemory
class MockProfileMemory:
    def load(self):
        return {
            "root": {"name": "User"},
            "relationships": {"location": "Athens, Greece"}, # Location stored here!
            "interests": {},
        }

async def run_test():
    profile = MockProfileMemory()
    print("--- TEST 1: check_location (Should fail currently) ---")
    try:
        res = await tool_system_utility("check_location", "socks5://127.0.0.1:9050", profile_memory=profile)
        print(f"Result: {res}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n--- TEST 2: check_weather without explicit location (Should fail to find profile location) ---")
    try:
        # We expect this to fail or return "SYSTEM ERROR" because it won't check 'relationships'
        res = await tool_system_utility("check_weather", "socks5://127.0.0.1:9050", profile_memory=profile)
        print(f"Result: {res}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
