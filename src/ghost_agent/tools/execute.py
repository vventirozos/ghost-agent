import asyncio
import os
import re
import logging
import uuid
import datetime
from pathlib import Path
from typing import List
from ..utils.logging import Icons, pretty_log

async def tool_execute(filename: str, content: str, sandbox_dir: Path, sandbox_manager, scrapbook=None, args: List[str] = None, memory_dir: Path = None):
    # --- 🛡️ HIJACK LAYER: CODE SANITIZATION (Verbatim from Granite4) ---
    
    # 0. VALIDATION: Ensure we are only executing scripts
    ext = str(filename).split('.')[-1].lower()
    if ext not in ["py", "sh", "js"]:
        pretty_log("Execution Blocked", f"Invalid extension: .{ext}", level="WARNING", icon=Icons.STOP)
        
        tip = "To save data files like .json or .txt, you MUST use 'file_system(operation=\"write\", ...)' instead."
        if ext in ["png", "jpg", "jpeg", "pdf", "svg"]:
            tip = f"To create a .{ext} (plot/image), you MUST write a Python script that saves the file directly (e.g., using 'plt.savefig(\"{filename}\")') and then run that script using 'execute'."

        return (
            f"--- EXECUTION ERROR ---\n"
            f"SYSTEM ERROR: You are trying to use 'execute' on a .{ext} file. \n"
            f"The 'execute' tool is ONLY for running scripts (.py, .sh, .js).\n"
            f"SYSTEM TIP: {tip}"
        )

    # 1. Fix "Slash-N" Hallucination
    if "\\n" in content:
        content = content.replace("\\n", "\n")

    # 2. Fix Escaped Quotes (Precision Layer)
    # We only want to fix hallucinated escapes like \" or \' that appear in the middle of text
    # but LLMs often escape EVERYTHING when they think they are in a JSON string.
    # However, we must NOT unescape things that are meant to be escaped.
    # A common LLM hallucination is: print(\"hello\") -> print("hello")
    # We use a negative lookbehind to ensure we don't unescape \\' or \\"
    content = re.sub(r'(?<!\\)\\(?P<quote>[\'\"])', r'\1', content)

    # 3. Fix Raw Regex Strings (The r\pattern Crash)
    # If the LLM writes r\d+ instead of r'\d+', we try to wrap it.
    try:
        # Only wrap if it's r\ followed by word characters and not already quoted
        content = re.sub(r'(?<![\'"])r\\(\w+)', r"r'\1'", content)
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
            # --- LOG FAILURE FOR AGGREGATE ANALYSIS ---
            if memory_dir:
                try:
                    report_path = memory_dir / "failure_reports.jsonl"
                    report_entry = {
                        "timestamp": datetime.datetime.now().isoformat(),
                        "filename": filename,
                        "content": content,
                        "error": output,
                        "exit_code": exit_code
                    }
                    with open(report_path, "a") as f:
                        f.write(json.dumps(report_entry) + "\n")
                except Exception as e:
                    logger.error(f"Failed to log failure report: {e}")

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
            f"{diagnostic_info}\n"
            f"SYSTEM REMINDER: Execution finished. Check your ACTIVE STRATEGY & CHECKLIST for any remaining meta-tasks (like learning skills or updating profile) before ending the turn."
        )

    except Exception as e:
        return f"--- EXECUTION RESULT ---\nEXIT CODE: 1\nSTDOUT/STDERR:\nError: Execution failed: {e}"