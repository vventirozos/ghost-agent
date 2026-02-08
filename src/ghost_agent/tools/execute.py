import asyncio
import os
import re
import logging
from pathlib import Path
from typing import List
from ..utils.logging import Icons, pretty_log

async def tool_execute(filename: str, content: str, sandbox_dir: Path, sandbox_manager, scrapbook=None, args: List[str] = None):
    # --- 🛡️ ADVANCED CODE REPAIR & INJECTION LAYER ---
    
    # 1. Basic Cleaning
    content = content.replace("\r\n", "\n").replace("\\n", "\n")
    content = re.sub(r'\\+([\'"])', r'\1', content)
    content = re.sub(r'^```[a-zA-Z]*\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'```$', '', content, flags=re.MULTILINE)

    # 2. Fix "Naked Raw/F-String Prefixes"
    content = re.sub(r'(?<![\'"])\b(rf|fr|r|f)(\\[^\s\),\'"]+)', r"\1'\2'", content)

    # 3. Fix Nested F-String Quote Collisions
    def repair_fstring(match):
        prefix, quote, body = match.groups()
        def fix_expr(m):
            return "{" + m.group(1).replace(quote, "\\" + quote) + "}"
        # Move the sub call out of the f-string to avoid backslash error in pre-3.12 Python
        repaired_body = re.sub(r'\{(.*?)\}', fix_expr, body)
        return f"{prefix}{quote}{repaired_body}{quote}"
    content = re.sub(r'\b(f|rf|fr)(["\'])(.*?)\2', repair_fstring, content)

    # 4. SCRAPBOOK VARIABLE INJECTION (The "NameError" Fix)
    if scrapbook and hasattr(scrapbook, '_data'):
        injected_vars = []
        # Find all words in the code that could be variables
        code_words = set(re.findall(r'\b[a-zA-Z_]\w*\b', content))
        for key, val in scrapbook._data.items():
            # If the variable is used in the code but not assigned in the code
            if key in code_words and not re.search(rf'^{key}\s*=', content, re.MULTILINE):
                # Format value for injection (strings get quotes, others raw)
                safe_val = f"'{val}'" if isinstance(val, str) and not (val.startswith("'") or val.startswith('"')) else val
                injected_vars.append(f"{key} = {safe_val}")
        
        if injected_vars:
            content = "# Injected from Scrapbook\n" + "\n".join(injected_vars) + "\n\n" + content

    # 5. Global Structural Repair (Truncation Fix)
    lines = content.split('\n')
    for i in range(len(lines)):
        l = lines[i].rstrip()
        if not l or l.startswith("#"): continue
        for q in ["'", '"']:
            if l.count(q) % 2 != 0:
                if l.endswith(")"): l = l[:-1] + q + ")"
                else: l = l + q
        lines[i] = l
    content = "\n".join(lines)
    
    # Close open blocks
    content += ")" * (content.count("(") - content.count(")"))
    content += "]" * (content.count("[") - content.count("]"))

    content = content.strip()
    # --------------------------------------------------

    pretty_log("Execution Task", filename, icon=Icons.TOOL_CODE)
    
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