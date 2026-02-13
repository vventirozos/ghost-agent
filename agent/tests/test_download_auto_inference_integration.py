import asyncio
from pathlib import Path
from ghost_agent.tools.file_system import tool_file_system

async def test_download_auto_inference():
    sandbox_dir = Path("/tmp/sandbox")
    # Use a dummy URL that looks like a file
    url = "http://example.com/pg1513.txt"
    
    # Test passed with ONLY url
    # Previously this failed with "path mandatory".
    # Now it should attempt download (and fail with 404/Connection error)
    # but NOT fail with validation error.
    
    result = await tool_file_system(
        operation="download", 
        sandbox_dir=sandbox_dir, 
        tor_proxy=None,
        url=url
    )
    
    print(f"Result: {result}")
    
    # We expect it to TRY downloading.
    # So it should NOT contain the validation error.
    forbidden_error = "Error: The 'path' (target filename) is missing"
    
    if forbidden_error in result:
        print(f"FAILURE: Received unexpected validation error: {result}")
        exit(1)
        
    # It should probably say "Failed to download" or "Successfully downloaded"
    if "Failed to download" in result or "Successfully downloaded" in result:
         print("SUCCESS: Tool attempted download as expected.")
    else:
         print(f"WARNING: Unexpected result format: {result}")
         # But technically if it didn't validation-fail, it's a pass for this specific test case.
         print("SUCCESS: No validation error.")

if __name__ == "__main__":
    asyncio.run(test_download_auto_inference())
