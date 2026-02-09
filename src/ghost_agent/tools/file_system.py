import asyncio
import hashlib
import os
import urllib.parse
import json
from pathlib import Path
from typing import Any
import httpx
from ..utils.logging import Icons, pretty_log

async def tool_read_file(filename: str, sandbox_dir: Path):
    pretty_log("File Read", filename, icon=Icons.TOOL_FILE_R)
    # GUARD 1: Stop model from trying to read URLs as files
    if str(filename).startswith("http"):
        return "Error: You are trying to use read_file on a URL. Use knowledge_base(action='ingest_document') instead."
    
    # GUARD 2: PDF files must be handled by the knowledge base
    if str(filename).lower().endswith(".pdf"):
        return f"Error: '{filename}' is a PDF. You cannot use read_file on PDFs. Use knowledge_base(action='recall', content='query') or knowledge_base(action='ingest_document') instead."

    try:
        # STRIP LEADING SLASH to prevent absolute path escapes
        path = sandbox_dir / str(filename).lstrip("/")
        if not path.exists(): return f"Error: '{filename}' not found."
        content = await asyncio.to_thread(path.read_text)
        return content
    except Exception as e: return f"Error: {e}"

async def tool_write_file(filename: str, content: Any, sandbox_dir: Path):
    pretty_log("File Write", filename, icon=Icons.TOOL_FILE_W)
    try:
        if content is None or str(content).strip().lower() == "none" or str(content).strip() == "":
            return f"Error: You are trying to write 'None' or empty data to '{filename}'. This usually means a previous tool (like search) failed. Check your data before writing."

        # Auto-serialize if the LLM sends a JSON object/list instead of a string
        if isinstance(content, (dict, list)):
            content = json.dumps(content, indent=2)
        elif not isinstance(content, str):
            content = str(content)

        # STRIP LEADING SLASH to prevent absolute path escapes
        path = sandbox_dir / str(filename).lstrip("/")
        # SELF-HEALING: Auto-create parent directories
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, content)
        return f"SUCCESS: Wrote {len(content)} chars to '{filename}'."
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
                
                # Strip leading slash to keep it inside sandbox
                clean_filename = str(filename).lstrip("/")
                target_path = sandbox_dir / clean_filename
                target_path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(target_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
                        
        return f"SUCCESS: Downloaded '{url}' to '{clean_filename}'."
    except Exception as e: return f"Error: {e}"

async def tool_file_search(pattern: str, sandbox_dir: Path, filename: str = None):
    # 1. Safety check for None
    if not pattern: return "Error: 'content' (search pattern) is required."
    
    # 2. Clean filename and pattern from model-injected artifacts
    if filename: 
        filename = str(filename).strip().lstrip("/")
    
    pattern = str(pattern).strip("'\"") # Strip accidental quotes
    
    search_root = (sandbox_dir / filename) if filename else sandbox_dir
    pretty_log("File Search", f"'{pattern}' in {search_root.name}/", icon=Icons.TOOL_FILE_S)
    
    try:
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
    except Exception as e: return f"Error: {e}"

async def tool_inspect_file(filename: str, sandbox_dir: Path, lines: int = 10):
    if not filename: return "Error: 'path' (filename) is required for inspection."
    pretty_log("File Peek", filename, icon=Icons.TOOL_FILE_I)
    try:
        path = sandbox_dir / str(filename).lstrip("/")
        if not path.exists(): return f"Error: '{filename}' not found."
        content = []
        with open(path, 'r', errors='ignore') as f:
            for _ in range(lines):
                line = f.readline()
                if not line: break
                content.append(line.strip())
        return "\n".join(content)
    except Exception as e: return f"Error: {e}"

async def tool_file_system(operation: str, sandbox_dir: Path, tor_proxy: str, path: str = None, content: str = None, **kwargs):
    # Unified mapping for common parameter hallucinations
    url = kwargs.get("url") or (path if path and str(path).startswith("http") else None)
    target_path = path or kwargs.get("filename") or kwargs.get("path")
    final_content = content or kwargs.get("data") or kwargs.get("content")

    if operation == "list": return await tool_list_files(sandbox_dir)
    if operation == "search": return await tool_file_search(final_content, sandbox_dir, target_path)
    if operation == "inspect": return await tool_inspect_file(target_path, sandbox_dir)
    
    if operation == "download":
        if not url: return "Error: 'url' parameter is required for download."
        # If target_path is the same as url, it means the model didn't provide a specific filename
        final_filename = target_path if target_path != url else (kwargs.get("filename") or kwargs.get("content"))
        if final_filename == url: final_filename = None
        return await tool_download_file(url=str(url), sandbox_dir=sandbox_dir, tor_proxy=tor_proxy, filename=final_filename)

    if not target_path: return f"Error: 'path' (filename) is required for {operation}"
    if operation == "read": return await tool_read_file(target_path, sandbox_dir)
    if operation == "write": return await tool_write_file(target_path, final_content, sandbox_dir)
    
    return f"Unknown operation: {operation}"