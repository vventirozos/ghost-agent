import asyncio
import subprocess
import os
import signal
import fcntl
from typing import Optional, Tuple
from ..utils.logging import Icons, pretty_log

class ShellSession:
    _instance = None

    def __init__(self):
        self.process = None
        self.cwd = os.getcwd()
        self.env = os.environ.copy()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ShellSession()
        return cls._instance

async def tool_shell(command: str, timeout: int = 10):
    """
    Executes a shell command in a persistent session.
    - Maintains CWD (Current Working Directory).
    - Maintains Environment Variables.
    - Supports `cd`.
    """
    session = ShellSession.get_instance()
    pretty_log("Shell Exec", f"[{session.cwd}] $ {command}", icon=Icons.TOOL_CODE)

    # Handle 'cd' builtin manually
    if command.strip().startswith("cd "):
        target_dir = command.strip()[3:].strip()
        # Handle ~ expansion
        if target_dir.startswith("~"):
            target_dir = os.path.expanduser(target_dir)
        
        # Resolve relative paths against current CWD
        new_path = os.path.abspath(os.path.join(session.cwd, target_dir))
        
        if os.path.isdir(new_path):
            session.cwd = new_path
            return f"Changed directory to: {session.cwd}"
        else:
            return f"Error: Directory not found: {new_path}"

    # For other commands, run in the current CWD
    try:
        # We use a simple subprocess.run for now, but forced relative to the session CWD
        # A true persistent PTY is complex (requires running bash in background and piping).
        # For reliability, we will stick to "Stateful CWD + One-Shot Execution" first.
        # This covers 99% of "persistent shell" needs (running subsequent commands in the same dir).

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=session.cwd,
            env=session.env
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                # Wait for process to die and close pipes (communicate handles cleanup)
                await asyncio.wait_for(proc.communicate(), timeout=1.0) 
            except: pass
            return f"Error: Command timed out after {timeout} seconds."

        output = stdout.decode().strip()
        error = stderr.decode().strip()
        
        result = ""
        if output: result += output
        if error: result += f"\nSTDERR: {error}"
        
        if not result and proc.returncode == 0:
            result = "(Command executed successfully with no output)"
        
        return result

    except Exception as e:
        return f"Shell Error: {e}"
