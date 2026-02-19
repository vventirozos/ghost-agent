
import pytest
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from ghost_agent.tools.execute import tool_execute

@pytest.mark.asyncio
async def test_tool_execute_shell_argument_escaping():
    """Test that arguments passed to tool_execute are safely escaped."""
    sandbox_dir = Path("/tmp/sandbox")
    sandbox_manager = MagicMock()
    # Mock execute result
    sandbox_manager.execute = AsyncMock(return_value=("output", 0))
    
    filename = "script.sh"
    content = "echo $1"
    
    # Malicious argument that tries to inject a command
    # If unescaped, this might become: ./script.sh '; rm -rf /'
    malicious_arg = "'; echo HACKED; '"
    args = [malicious_arg]
    
    # We mock the filesystem writes to avoid actual file creation
    with patch("asyncio.to_thread", side_effect=lambda func, *a, **k: func(*a, **k) if not asyncio.iscoroutinefunction(func) else func(*a, **k)) as mock_to_thread:
        # We need to mock Path.write_text and os.chmod
        with patch.object(Path, "write_text"), patch("os.chmod"):
             # We also need to mock valid file extension check or let it pass
             # The tool checks extension. .sh is valid.
             
             # The important part is verifying the command sent to sandbox_manager.execute
             # but tool_execute wraps the command in a wrapper script _run_xxxx.sh
             # and sends THAT to execute.
             # So we need to inspect the content written to the wrapper script.
             
             # Wait, tool_execute logic:
             # wrapper_path.write_text(f"#!/bin/sh\n{cmd}\n")
             # So we need to capture what was written to wrapper_path.write_text
             
             # Let's mock the Path object created for wrapper
             # But wrapper path is created dynamically: sandbox_dir / wrapper_name
             
             # We can mock Path.write_text.
             # One call is for the script itself (content).
             # Another call is for the wrapper script.
             
             mock_write_text = MagicMock()
             with patch.object(Path, "write_text", mock_write_text):
                 await tool_execute(filename, content, sandbox_dir, sandbox_manager, args=args)
                 
                 # Analyze calls to write_text
                 # Call 1: user script content
                 # Call 2: wrapper script content
                 
                 # Find the call that looks like a wrapper script
                 wrapper_call = None
                 for call in mock_write_text.call_args_list:
                     args_call, _ = call
                     text = args_call[0]
                     if "#!/bin/sh" in text:
                         wrapper_call = text
                         break
                 
                 assert wrapper_call is not None, "Wrapper script was not written"
                 
                 # Check if the malicious arg is properly quoted.
                 # Python's shlex.quote("'; echo HACKED; '") -> "''\"'\"'; echo HACKED; '\"'\"''" (or similar safe quoting)
                 # The insecure version would just be: ... ' '; echo HACKED; ' ' ...
                 
                 # In the insecure version: 
                 # cmd = "./script.sh '; echo HACKED; '"
                 # (If logic is: str(a).replace("'", "'\\''")) -> '; echo HACKED; ' -> no outer quotes?
                 # Actually the current logic is: cmd += " " + " ".join([str(a).replace("'", "'\\''") for a in args])
                 # It converts ' to '\'' but DOES NOT wrap the arg in quotes itself!
                 # So: arg = foo -> cmd = ./script foo
                 # arg = foo bar -> cmd = ./script foo bar (2 args!) -> INJECTION/SPLITTING
                 # arg = '; ls' -> cmd = ./script ; ls -> INJECTION
                 
                 # With shlex.quote:
                 # arg = '; ls' -> cmd = ./script '; ls' -> passed as 1 arg, safe.
                 
                 # Verify we have quotes around the arg or at least it is safe.
                 # The malicious arg contains space and semicolon. It MUST be quoted.
                 assert "'" in wrapper_call or '"' in wrapper_call
                 # Specifically, the semicolon should be quoted or escaped.
                 
                 # Let's be specific about the failure mode of the OLD code vs NEW code.
                 # OLD: ./script.sh '; echo HACKED; '
                 # NEW: ./script.sh ''; echo HACKED; '' (quoted)
                 
                 # Ideally, we verify that the command passed to the shell treats it as a single argument.
                 # We can check if shlex.split(command_line) results in the expected args.
                 
                 # Extract command line from wrapper script
                 # Wrapper content: "#!/bin/sh\n./script.sh arg1 ...\n"
                 lines = wrapper_call.splitlines()
                 cmd_line = lines[1] # The command
                 
                 import shlex
                 parts = shlex.split(cmd_line)
                 
                 # The command for .sh files is "bash script.sh arg1 ..."
                 # So parts[0] is "bash", parts[1] is script name, parts[2:] are args
                 if parts[0] == "bash":
                     script = parts[1]
                     captured_args = parts[2:]
                 else:
                     # For other executables or if logic changes
                     script = parts[0]
                     captured_args = parts[1:]
                 
                 assert len(captured_args) == 1, f"Argument splitting occurred! Parts: {captured_args}"
                 # The captured arg should be the ORIGINAL string, because shlex.split REVERSES the quoting.
                 # shlex.quote("'foo'") -> "''\"'\"'foo'\"'\"''"
                 # shlex.split("cmd ''\"'\"'foo'\"'\"''") -> ["cmd", "'foo'"]
                 # So captured_args[0] should equal malicious_arg
                 assert captured_args[0] == malicious_arg, f"Argument corrupted! Got: {captured_args[0]}"

