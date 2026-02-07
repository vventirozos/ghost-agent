import asyncio
import os
import re
import logging
from pathlib import Path
from typing import List
from ..utils.logging import Icons, pretty_log

async def tool_execute(filename: str, content: str, sandbox_dir: Path, sandbox_manager, args: List[str] = None):
    # --- 🛡️ HIJACK LAYER: CODE SANITIZATION (Verbatim from original script) ---
    # Granite often produces JSON artifacts. We must unroll them before execution.
    
    # 1. Fix "Slash-N" Hallucination (Literal \n in code)
    if "\\n" in content:
        content = content.replace("\\n", "\n")

    # 2. Fix Escaped Quotes (The Docstring Crash)
    content = content.replace('\\"', '"')
    content = content.replace("\\'", "'")

    # 3. Fix Raw Regex Strings (The r\pattern Crash)
    try:
        content = re.sub(r'(?<![\'"])r\\([^\s\),]+)', r"r'''\\\1'''", content)
    except Exception:
        pass 

    # 4. Remove Markdown Wrappers (Common "Chatty" artifact)
    content = re.sub(r'^```[a-zA-Z]*\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'```$', '', content, flags=re.MULTILINE)

    # 5. Final Trim
    content = content.strip()
    # ----------------------------------------

    pretty_log("Execution Run", filename, icon=Icons.TOOL_CODE)
    
    if not sandbox_manager: return "Error: Sandbox manager not initialized."
    
    # STRIP LEADING SLASH to prevent absolute path escapes
    rel_path = str(filename).lstrip("/")
    host_path = sandbox_dir / rel_path
    
    # SELF-HEALING: Auto-create directories
    host_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write sanitized content
    try:
        await asyncio.to_thread(host_path.write_text, content)
    except Exception as e:
        return f"Error writing script: {e}"

    try:
        # Check if it's a python file or other
        # Use the sanitized relative path for the command
        if rel_path.endswith(".py"):
            cmd = ["python3", rel_path]
        elif rel_path.endswith(".sh"):
            cmd = ["bash", rel_path]
        else:
            cmd = ["python3", rel_path] # Default to python

        if args: cmd.extend(args)

        # FIX: Correct method name is 'execute', and it returns (output, exit_code)
        output, exit_code = await asyncio.to_thread(sandbox_manager.execute, " ".join(cmd))
        
        return f"--- EXECUTION RESULT ---\nEXIT CODE: {exit_code}\nSTDOUT/STDERR:\n{output}"

    except Exception as e:
        # Improved error string so agent.py can parse it
        return f"--- EXECUTION RESULT ---\nEXIT CODE: 1\nSTDOUT/STDERR:\nError: Execution failed: {e}"

        