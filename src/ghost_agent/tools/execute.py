import asyncio
import os
import re
import logging
import uuid
import datetime
import ast
import json
from pathlib import Path
from typing import List
from ..utils.logging import Icons, pretty_log
from ..utils.sanitizer import sanitize_code

async def tool_execute(filename: str, content: str, sandbox_dir: Path, sandbox_manager, scrapbook=None, args: List[str] = None, memory_dir: Path = None):
    # --- 🛡️ HIJACK LAYER: CODE SANITIZATION ---
    
    # Helper for consistent error reporting
    def _format_error(msg):
        return f"--- EXECUTION RESULT ---\nEXIT CODE: 1\nSTDOUT/STDERR:\n{msg}"

    # 0. VALIDATION: Ensure we are only executing scripts
    ext = str(filename).split('.')[-1].lower()
    if ext not in ["py", "sh", "js"]:
        pretty_log("Execution Blocked", f"Invalid extension: .{ext}", level="WARNING", icon=Icons.STOP)
        tip = "To save data files, use 'file_system(operation=\"write\", ...)' instead."
        return _format_error(f"SYSTEM ERROR: The 'execute' tool is ONLY for running scripts (.py, .sh, .js).\nSYSTEM TIP: {tip}")

    # 1. Holistic Sanitization
    content, syntax_error = sanitize_code(content, str(filename))
    
    if syntax_error:
        # We block execution if syntax is clearly invalid to save a roundtrip
        pretty_log("Sanitization Failed", syntax_error, level="WARNING", icon=Icons.BUG)
        return _format_error(f"Syntax Error Detected: {syntax_error}\nPlease fix the code and try again.")

    # 3. Final Trim
    content = content.strip()
    # ----------------------------------------
    pretty_log("Execution Task", filename, icon=Icons.TOOL_CODE)
    
    if not sandbox_manager: return _format_error("Error: Sandbox manager not initialized.")
    rel_path = str(filename).lstrip("/")
    host_path = sandbox_dir / rel_path
    
    # Stubbornness Guard
    if host_path.exists():
        try:
            if "".join(host_path.read_text().split()) == "".join(content.split()):
                return "--- EXECUTION RESULT ---\nEXIT CODE: 1\nSTDOUT/STDERR:\nSYSTEM ERROR: EXACT SAME CODE SUBMITTED. Change your logic.\n"
        except: pass

    host_path.parent.mkdir(parents=True, exist_ok=True)
    try: await asyncio.to_thread(host_path.write_text, content)
    except Exception as e: return _format_error(f"Error writing script: {e}")

    if rel_path.endswith(".py"):
        await asyncio.to_thread(sandbox_manager.execute, f"python3 -m black {rel_path}", timeout=15)

    try:
        ext = rel_path.split('.')[-1].lower()
        runtime_map = {"py": "python3 -u", "js": "node", "sh": "bash"}
        runner = runtime_map.get(ext, "chmod +x" if ext == "sh" else "")
        cmd = f"{runner} {rel_path}" if runner else f"./{rel_path}"
        if args: cmd += " " + " ".join([str(a).replace("'", "'\\''") for a in args])

        wrapper_name = f"_run_{uuid.uuid4().hex[:6]}.sh"
        wrapper_path = sandbox_dir / wrapper_name
        wrapper_path.write_text(f"#!/bin/sh\n{cmd}\n")
        os.chmod(wrapper_path, 0o777)
        output, exit_code = await asyncio.to_thread(sandbox_manager.execute, f"./{wrapper_name}")
        wrapper_path.unlink(missing_ok=True)
        
        diagnostic_info = ""
        if exit_code != 0:
            tb_match = re.findall(r'File "([^"]+)", line (\d+),', output)
            if tb_match:
                _, last_error_line = tb_match[-1]
                try:
                    line_num = int(last_error_line)
                    lines = content.splitlines()
                    start_l = max(0, line_num - 3)
                    end_l = min(len(lines), line_num + 2)
                    snippet = "\n".join([f"{i+1}: {l}" for i, l in enumerate(lines) if start_l <= i < end_l])
                    diagnostic_info = f"\n--- BUG LOCATION (Line {line_num}) ---\n{snippet}\n----------------------------------\n"
                except: pass

        return f"--- EXECUTION RESULT ---\nEXIT CODE: {exit_code}\nSTDOUT/STDERR:\n{output}\n{diagnostic_info}"
    except Exception as e:
        return f"--- EXECUTION RESULT ---\nEXIT CODE: 1\nSTDOUT/STDERR:\nError: {e}"
