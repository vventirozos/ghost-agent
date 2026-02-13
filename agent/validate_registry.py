
import sys
import json
try:
    from ghost_agent.tools.registry import TOOL_DEFINITIONS
    print("Successfully imported TOOL_DEFINITIONS")
    print(f"Loaded {len(TOOL_DEFINITIONS)} tools.")
    # Check if a specific tool exists and has correct schema
    fs = next((t for t in TOOL_DEFINITIONS if t["function"]["name"] == "file_system"), None)
    if fs:
        print("FileSystem tool schema verified.")
        print(json.dumps(fs, indent=2))
    else:
        print("ERROR: FileSystem tool not found!")
        sys.exit(1)
except Exception as e:
    print(f"Failed to load registry: {e}")
    sys.exit(1)
