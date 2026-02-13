import asyncio
import os
from pathlib import Path

# Mock dependencies
class MockContext:
    def __init__(self):
        self.sandbox_dir = Path("/tmp/ghost_test_sandbox")
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        self.tor_proxy = None

async def test_move_operation():
    # Setup
    from ghost_agent.tools.file_system import tool_move_file, _get_safe_path
    
    ctx = MockContext()
    source_file = "test_source.txt"
    dest_file = "test_dest.txt"
    
    # create source file
    src_path = ctx.sandbox_dir / source_file
    with open(src_path, "w") as f:
        f.write("test content")

    print(f"Created source: {src_path}")
    
    # Run move
    result = await tool_move_file(source_file, dest_file, ctx.sandbox_dir)
    print(f"Result: {result}")
    
    # Verify
    dst_path = ctx.sandbox_dir / dest_file
    if not src_path.exists() and dst_path.exists():
        print(f"SUCCESS: File moved from {source_file} to {dest_file}")
    else:
        print(f"FAILURE: Source exists: {src_path.exists()}, Dest exists: {dst_path.exists()}")

    # cleanup
    if dst_path.exists(): os.remove(dst_path)
    if src_path.exists(): os.remove(src_path)

if __name__ == "__main__":
    asyncio.run(test_move_operation())
