import asyncio
import os
import re
import logging
import uuid
import datetime
from pathlib import Path
from typing import List
from ..utils.logging import Icons, pretty_log

async def tool_execute(filename: str, content: str, sandbox_dir: Path, sandbox_manager, scrapbook=None, args: List[str] = None):
    # --- 🛡️ HIJACK LAYER: CODE SANITIZATION (Verbatim from Granite4) ---
    
    # 1. Fix "Slash-N" Hallucination
    if "\\n" in content:
        content = content.replace("\\n", "\n")

    # 2. Fix Escaped Quotes
    content = content.replace('\\"', '"')
    content = content.replace("\\'", "'")

    # 3. Fix Raw Regex Strings (The r\pattern Crash)
    try:
        content = re.sub(r'(?<![\'"])r\\([^\s\),]+)', r"r'''\\\1'''", content)
    except Exception:
        pass 

    # 4. Remove Markdown Wrappers
    content = re.sub(r'^```[a-zA-Z]*\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'```$', '', content, flags=re.MULTILINE)

    # 5. Final Trim
    content = content.strip()
    # ----------------------------------------

    pretty_log("Execution Task", filename, icon=Icons.TOOL_CODE)
    
    if not sandbox_manager: return "Error: Sandbox manager not initialized."
    
    # STRIP LEADING SLASH to prevent absolute path escapes
    rel_path = str(filename).lstrip("/")
    host_path = sandbox_dir / rel_path
    
    # --- STUBBORNNESS GUARD ---
    if host_path.exists():
        try:
            existing_code = host_path.read_text()
            if "".join(existing_code.split()) == "".join(content.split()):
                pretty_log("Stubbornness Guard", "Blocked identical code", level="WARNING", icon=Icons.STOP)
                return (
                    "--- EXECUTION RESULT ---\n"
                    "EXIT CODE: 1\n"
                    "STDOUT/STDERR:\n"
                    "SYSTEM ERROR: You submitted the EXACT SAME CODE that failed previously. You MUST change the logic.\n"
                )
        except: pass

    # SELF-HEALING: Auto-create directories
    host_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write sanitized content
    try:
        await asyncio.to_thread(host_path.write_text, content)
    except Exception as e:
        return f"Error writing script: {e}"

    # --- AUTO-FORMATTING ---
    if rel_path.endswith(".py"):
        await asyncio.to_thread(sandbox_manager.execute, f"python3 -m black {rel_path}", timeout=15)

    try:
        # EXECUTION COMMAND (Granite4 Wrapper Style)
        ext = rel_path.split('.')[-1].lower()
        runtime_map = {"py": "python3 -u", "js": "node", "sh": "bash"}
        runner = runtime_map.get(ext, "chmod +x" if ext == "sh" else "")
        cmd = f"{runner} {rel_path}" if runner else f"./{rel_path}"
        
        if args: 
            cmd += " " + " ".join([str(a).replace("'", "'\\''") for a in args])

        wrapper_name = f"_run_{uuid.uuid4().hex[:6]}.sh"
        wrapper_path = sandbox_dir / wrapper_name
        wrapper_path.write_text(f"#!/bin/sh\n{cmd}\n")
        os.chmod(wrapper_path, 0o777)

        output, exit_code = await asyncio.to_thread(sandbox_manager.execute, f"./{wrapper_name}")
        wrapper_path.unlink(missing_ok=True)
        
        # --- GRANITE-STYLE ERROR DIAGNOSTICS ---
        diagnostic_info = ""
        if exit_code != 0:
            tb_match = re.findall(r'File "([^"]+)", line (\d+),', output)
            if tb_match:
                last_error_file, last_error_line = tb_match[-1]
                if os.path.basename(last_error_file) == os.path.basename(rel_path):
                    try:
                        line_num = int(last_error_line)
                        lines = content.splitlines()
                        start_l = max(0, line_num - 3)
                        end_l = min(len(lines), line_num + 2)
                        snippet = "\n".join([f"{i+1}: {l}" for i, l in enumerate(lines) if start_l <= i < end_l])
                        
                        diagnostic_info = (
                            f"\n--- BUG LOCATION (Line {line_num}) ---\n"
                            f"{snippet}\n"
                            f"----------------------------------\n"
                            f"SYSTEM TIP: Look at Line {line_num} above."
                        )
                    except: pass

        return (
            f"--- EXECUTION RESULT ---\n"
            f"EXIT CODE: {exit_code}\n"
            f"STDOUT/STDERR:\n{output}\n"
            f"{diagnostic_info}"
        )

    except Exception as e:
        return f"--- EXECUTION RESULT ---\nEXIT CODE: 1\nSTDOUT/STDERR:\nError: Execution failed: {e}"