import asyncio
from pathlib import Path
from ghost_agent.tools.file_system import tool_file_system

# Mock tool_download_file to checking arguments
# We can't easily mock the import inside the module without patching, 
# so we'll just check the return value or side effects if possible.
# Actually, tool_file_system calls tool_download_file which returns a string string.
# But tool_download_file logic is:
# if filename == url: filename = basename(url)
# So if we pass path=URL and destination=WANTED, 
# The BUG is that filename becomes URL, so it becomes basename(URL).
# The FIX is that filename should be WANTED.

# We can intercept the call by patching ghost_agent.tools.file_system.tool_download_file
from unittest.mock import patch

async def run_test():
    sandbox_dir = Path("/tmp/sandbox")
    url = "http://example.com/pg1513.txt"
    wanted_filename = "romeo_source.txt"
    
    print(f"Testing download with path='{url}' and destination='{wanted_filename}'")
    
    with patch('ghost_agent.tools.file_system.tool_download_file') as mock_download:
        mock_download.return_value = "Mocked download"
        
        # Call with the problematic arguments
        # explicit 'path' as URL, and 'destination' as target
        await tool_file_system(
            operation="download", 
            sandbox_dir=sandbox_dir, 
            tor_proxy=None,
            path=url, 
            destination=wanted_filename
        )
        
        # Check what arguments tool_download_file was called with
        args, kwargs = mock_download.call_args
        
        # tool_download_file(url, sandbox_dir, tor_proxy, filename)
        # It might be called with positional or keyword args.
        # Signature: async def tool_download_file(url: str, sandbox_dir: Path, tor_proxy: str, filename: str = None):
        
        actual_filename = kwargs.get('filename')
        if not actual_filename and len(args) >= 4:
            actual_filename = args[3]
            
        print(f"Called with filename: {actual_filename}")
        
        if actual_filename == wanted_filename:
            print("SUCCESS: Filename was respected.")
        else:
            print(f"FAILURE: Filename was '{actual_filename}', expected '{wanted_filename}'.")
            print("This confirms the bug where 'path' (URL) shadows 'destination'.")

if __name__ == "__main__":
    asyncio.run(run_test())
