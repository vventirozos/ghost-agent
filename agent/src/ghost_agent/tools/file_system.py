import asyncio
import hashlib
import os
import urllib.parse
import json
from pathlib import Path
from typing import Any
import httpx
from ..utils.logging import Icons, pretty_log

def _get_safe_path(sandbox_dir: Path, filename: str) -> Path:
    """
    Safely resolves a path while preventing traversal attacks.
    """
    # 1. Strip leading slashes to treat as relative
    clean_name = str(filename).lstrip("/")
    
    # 2. Resolve to absolute path
    target_path = (sandbox_dir / clean_name).resolve()
    
    # 3. Ensure it's still inside sandbox
    if not str(target_path).startswith(str(sandbox_dir.resolve())):
        raise ValueError(f"Security Error: Path '{filename}' attempts to access outside sandbox.")
        
    return target_path

async def tool_read_file(filename: str, sandbox_dir: Path):
    pretty_log("File Read", filename, icon=Icons.TOOL_FILE_R)
    # GUARD 1: Stop model from trying to read URLs as files
    if str(filename).startswith("http"):
        return "Error: You are trying to use read_file on a URL. Use knowledge_base(action='ingest_document') instead."
    
    # GUARD 2: PDF files must be handled by the knowledge base
    if str(filename).lower().endswith(".pdf"):
        return f"Error: '{filename}' is a PDF. You cannot use read_file on PDFs. Use knowledge_base(action='recall', content='query') or knowledge_base(action='ingest_document') instead."

    try:
        path = _get_safe_path(sandbox_dir, filename)
        if not path.exists(): return f"Error: '{filename}' not found."
        content = await asyncio.to_thread(path.read_text)
        return content
    except ValueError as ve: return str(ve)
    except Exception as e: return f"Error: {e}"

async def tool_write_file(filename: str, content: Any, sandbox_dir: Path):
    pretty_log("File Write", filename, icon=Icons.TOOL_FILE_W)
    try:
        if content is None or str(content).strip().lower() == "none" or str(content).strip() == "":
            return f"Error: The 'content' you provided for '{filename}' is empty or 'None'. You MUST provide the actual text to write. If you intended to use data from a previous tool, ensure that tool succeeded and produced output."

        # Auto-serialize if the LLM sends a JSON object/list instead of a string
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2)
        elif not isinstance(content, str):
            content = str(content)

        path = _get_safe_path(sandbox_dir, filename)
        
        # SELF-HEALING: Auto-create parent directories
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, content)
        return f"SUCCESS: Wrote {len(content)} chars to '{filename}'."
    except ValueError as ve: return str(ve)
    except Exception as e: return f"Error: {e}"

async def tool_list_files(sandbox_dir: Path, memory_system=None):
    pretty_log("Sandbox Tree", "Listing workspace files", icon=Icons.TOOL_FILE_I)
    try:
        # Shallow listing like Granite4 for high performance
        files = os.listdir(sandbox_dir)
        tree = [f"📄 {f}" if (sandbox_dir / f).is_file() else f"📁 {f}" for f in sorted(files) if not f.startswith(".")]
        
        sandbox_tree = "\n".join(tree) if tree else "[Empty]"
        return f"CURRENT SANDBOX DIRECTORY STRUCTURE:\n{sandbox_tree}\n\n(Use these filenames for all file tools)"
    except Exception as e: return f"Error scanning sandbox: {e}"

async def tool_download_file(url: str, sandbox_dir: Path, tor_proxy: str, filename: str = None):
    # 1. Clean Proxy URL
    proxy_url = tor_proxy
    mode = "TOR" if proxy_url and "127.0.0.1" in proxy_url else "WEB"
    
    pretty_log(f"Download [{mode}]", f"{url[:35]}..", icon=Icons.TOOL_DOWN)
    
    if proxy_url and proxy_url.startswith("socks5://"): 
        proxy_url = proxy_url.replace("socks5://", "socks5h://")

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(proxy=proxy_url, headers=headers, follow_redirects=True, timeout=60.0) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200: return f"Error {resp.status_code} - Failed to download from {url}"
                
                # If filename is None, empty, or exactly the URL, extract from URL path
                if not filename or str(filename).strip() == "" or filename == url:
                    filename = os.path.basename(urllib.parse.urlparse(url).path) or "file.dat"
                
                # Use safe path
                try:
                    target_path = _get_safe_path(sandbox_dir, filename)
                except ValueError as ve: return str(ve)

                target_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(target_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
                        
        return f"SUCCESS: Downloaded '{url}' to '{filename}'."
    except Exception as e: return f"Error: {e}"

async def tool_file_search(pattern: str, sandbox_dir: Path, filename: str = None):
    # 1. Safety check for None
    if not pattern: return "Error: 'content' (search pattern) is required."
    
    try:
        # 2. Clean filename and pattern from model-injected artifacts
        if filename: 
            search_root = _get_safe_path(sandbox_dir, filename)
        else:
            search_root = sandbox_dir
    
        pattern = str(pattern).strip("'\"") # Strip accidental quotes
        
        pretty_log("File Search", f"'{pattern}' in {search_root.name}/", icon=Icons.TOOL_FILE_S)
    
        results = []
        if search_root.is_file():
            files = [search_root]
        else:
            files = list(search_root.rglob("*"))
            
        for fpath in files:
            if not fpath.is_file() or fpath.suffix.lower() in ['.pdf', '.bin', '.pyc']: continue
            try:
                with open(fpath, 'r', errors='ignore') as f:
                    for i, line in enumerate(f):
                        if pattern.lower() in line.lower():
                            results.append(f"[{fpath.relative_to(sandbox_dir)}:{i+1}] {line.strip()}")
                            if len(results) > 15: break
            except: pass
            if len(results) > 15: break
            
        return "\n".join(results) if results else "Report: No matches found. (Tip: Use list_files to verify the path)"
    except ValueError as ve: return str(ve)
    except Exception as e: return f"Error: {e}"

async def tool_inspect_file(filename: str, sandbox_dir: Path, lines: int = 10):
    if not filename: return "Error: 'path' (filename) is required for inspection."
    pretty_log("File Peek", filename, icon=Icons.TOOL_FILE_I)
    try:
        path = _get_safe_path(sandbox_dir, filename)
        if not path.exists(): return f"Error: '{filename}' not found."
        content = []
        with open(path, 'r', errors='ignore') as f:
            for _ in range(lines):
                line = f.readline()
                if not line: break
                content.append(line.strip())
        return "\n".join(content)
    except ValueError as ve: return str(ve)
    except Exception as e: return f"Error: {e}"

async def tool_move_file(source: str, destination: str, sandbox_dir: Path) -> str:
    """
    Moves/Renames a file from source to destination within the sandbox.
    """
    try:
        src_path = _get_safe_path(sandbox_dir, source)
        dst_path = _get_safe_path(sandbox_dir, destination)

        if not src_path.exists():
            return f"Error: Source file '{source}' not found."
        
        # Overwrite if exists, consistent with 'mv' command expectations in this context
        # if dst_path.exists():
        #     return f"Error: Destination file '{destination}' already exists."

        import shutil
        shutil.move(src_path, dst_path)
        return f"Successfully moved '{source}' to '{destination}'."
    except Exception as e:
        return f"Error moving file: {e}"

async def tool_file_system(operation: str, sandbox_dir: Path, tor_proxy: str, path: str = None, content: str = None, **kwargs):
    pretty_log("Tool Call Args", f"Op={operation}, Path={path}, ContentLen={len(content) if content else 0}, Kwargs={kwargs}")
    # Unified mapping for common parameter hallucinations
    url = kwargs.get("url") or (path if path and str(path).startswith("http") else None)
    
    potential_path = path if path != url else None
    target_path = potential_path or kwargs.get("filename") or kwargs.get("path") or kwargs.get("destination") or kwargs.get("file") or kwargs.get("outfile") or kwargs.get("output")
    final_content = content or kwargs.get("data") or kwargs.get("content") or kwargs.get("text")

    # --- HALLUCINATION HEALING ---
    # If the LLM used 'url' as a filename for a non-download operation
    if not target_path and url and operation != "download":
        target_path = url
        url = None
    
    # If the LLM put the content in 'path' but didn't provide 'content' (common for write)
    if operation == "write" and target_path and not final_content:
        # Check if the LLM accidentally sent the content as the only other parameter
        pass

    if operation == "list": return await tool_list_files(sandbox_dir)
    if operation == "search": return await tool_file_search(final_content, sandbox_dir, target_path)
    if operation == "inspect": return await tool_inspect_file(target_path, sandbox_dir)
    
    if operation == "download":
        if not url: return "Error: The 'url' parameter is MANDATORY for download operations."
        if not target_path:
            # Automatic inference from URL
            parsed = urllib.parse.urlparse(str(url))
            target_path = os.path.basename(parsed.path)
            if not target_path: target_path = "downloaded_file"
        return await tool_download_file(url=str(url), sandbox_dir=sandbox_dir, tor_proxy=tor_proxy, filename=target_path)

    if not target_path: 
        return f"Error: The 'path' (target filename) is missing for the '{operation}' operation. You MUST specify WHICH file to {operation}."
    
    if operation == "read": return await tool_read_file(target_path, sandbox_dir)
    if operation == "write": return await tool_write_file(target_path, final_content, sandbox_dir)
    
    if operation == "move":
        destination = kwargs.get("destination")
        if not destination: return "Error: 'destination' argument is MANDATORY for 'move' operation."
        if not target_path: return "Error: 'path' (source) argument is MANDATORY for 'move' operation."
        return await tool_move_file(target_path, destination, sandbox_dir)

    return f"Unknown operation: {operation}"