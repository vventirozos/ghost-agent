import asyncio
from pathlib import Path
from ghost_agent.tools.file_system import tool_file_system

async def test_download_mandatory_path():
    sandbox_dir = Path("/tmp/sandbox")
    url = "http://example.com/pg1513.txt"
    
    # Test passed with ONLY url (should fail now)
    result = await tool_file_system(
        operation="download", 
        sandbox_dir=sandbox_dir, 
        tor_proxy=None,
        url=url
    )
    
    print(f"Result: {result}")
    
    expected_error = "Error: You must specify the destination filename"
    if expected_error in result:
        print("SUCCESS: Error message received as expected.")
    else:
        print("FAILURE: Did not receive expected error message.")
        exit(1)

if __name__ == "__main__":
    asyncio.run(test_download_mandatory_path())
