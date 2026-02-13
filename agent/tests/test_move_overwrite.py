import asyncio
import os
from pathlib import Path

# Mock dependencies
class MockContext:
    def __init__(self):
        self.sandbox_dir = Path("/tmp/ghost_test_sandbox_overwrite")
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.tor_proxy = None

async def test_move_overwrite():
    # Setup
    from ghost_agent.tools.file_system import tool_move_file, _get_safe_path
    
    ctx = MockContext()
    source_file = "src_overwrite.txt"
    dest_file = "dst_overwrite.txt"
    
    # Create source
    src_path = ctx.sandbox_dir / source_file
    with open(src_path, "w") as f:
        f.write("NEW CONTENT")

    # Create EXISTING destination (to be overwritten)
    dst_path = ctx.sandbox_dir / dest_file
    with open(dst_path, "w") as f:
        f.write("OLD CONTENT")

    print(f"Created source: {src_path}")
    print(f"Created existing dest: {dst_path}")
    
    # Run move
    result = await tool_move_file(source_file, dest_file, ctx.sandbox_dir)
    print(f"Result: {result}")
    
    # Verify
    if not src_path.exists():
        with open(dst_path, "r") as f:
            content = f.read()
        if content == "NEW CONTENT":
            print(f"SUCCESS: Destination overwritten with correct content.")
        else:
            print(f"FAILURE: Content mismatch. Expected 'NEW CONTENT', got '{content}'")
    else:
        print(f"FAILURE: Source file still exists.")

    # cleanup
    if dst_path.exists(): os.remove(dst_path)
    if src_path.exists(): os.remove(src_path)

if __name__ == "__main__":
    asyncio.run(test_move_overwrite())
