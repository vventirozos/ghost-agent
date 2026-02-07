import datetime
import json
import logging
import os
import contextvars
from typing import Any, Optional

request_id_context = contextvars.ContextVar("request_id", default="SYSTEM")
LOG_TRUNCATE_LIMIT = 300
DEBUG_MODE = False 

class Icons:
    # --- Lifecycle ---
    SYSTEM_BOOT  = "⚡"
    SYSTEM_READY = "🚀"
    SYSTEM_SHUT  = "💤"
    
    # --- Request Flow ---
    REQ_START    = "🎬"
    REQ_DONE     = "🏁"
    REQ_WAIT     = "⏳"

    # --- Brain ---
    BRAIN_THINK  = "💭"
    BRAIN_PLAN   = "📋"
    BRAIN_CTX    = "🧩"
    LLM_ASK      = "🗣️"
    LLM_REPLY    = "🤖"
    
    # --- Specialized Tools ---
    TOOL_SEARCH  = "🌐"
    TOOL_DEEP    = "🔬"
    TOOL_CODE    = "🐍"
    TOOL_SHELL   = "🐚"
    TOOL_FILE_W  = "💾"
    TOOL_FILE_R  = "📖"
    TOOL_FILE_S  = "🔍"
    TOOL_FILE_I  = "👀"
    TOOL_DOWN    = "⬇️"
    
    # --- Memory & Identity ---
    MEM_SAVE     = "📝"
    MEM_READ     = "🔎"
    MEM_MATCH    = "📍"
    MEM_INGEST   = "📚"
    MEM_SPLIT    = "✂️"
    MEM_EMBED    = "🧬"
    MEM_WIPE     = "🧹"
    USER_ID      = "👤"
    
    # --- Status ---
    OK           = "✅"
    FAIL         = "❌"
    WARN         = "⚠️"
    STOP         = "🛑"
    RETRY        = "🔄"
    IDEA         = "💡"

logger = logging.getLogger("GhostAgent")

def setup_logging(log_file: str, debug: bool = False, daemon: bool = False):
    global DEBUG_MODE
    DEBUG_MODE = debug
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')

    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    if not daemon:
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        sh.setLevel(logging.DEBUG if debug else logging.INFO)
        logger.addHandler(sh)

    for lib in ["httpx", "uvicorn", "docker", "chromadb", "urllib3", "pypdf"]:
        logging.getLogger(lib).setLevel(logging.WARNING)

def pretty_log(title: str, content: Any = None, icon: str = "📝", level: str = "INFO", special_marker: str = None):
    req_id = request_id_context.get()
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")

    # Fixed-width alignment for the header
    # [LEVEL] ICON HH:MM:SS - [REQ_ID] TITLE
    
    if special_marker == "BEGIN":
        print(f"[{level:5}] {Icons.REQ_START} {timestamp} - [{req_id}] {'='*10} REQUEST STARTED {'='*10}", flush=True)
        return
    if special_marker == "END":
        print(f"[{level:5}] {Icons.REQ_DONE} {timestamp} - [{req_id}] {'='*10} REQUEST FINISHED {'='*10}", flush=True)
        return

    # Pad title to ensure alignment
    # We use 25 characters for the title field
    log_line = f"[{level:5}] {icon} {timestamp} - [{req_id}] {title.upper():<25}"
    
    if content is not None and not isinstance(content, (dict, list)):
        log_line += f" : {str(content)}"
        print(log_line, flush=True)
    else:
        print(log_line, flush=True)
        if content is not None:
            # Multi-line or complex data
            try: content_str = json.dumps(content, indent=2, default=str)
            except: content_str = str(content)
            
            logger.debug(f"DETAILS FOR [{req_id}] {title}: {content_str}")
            if level == "ERROR" or DEBUG_MODE:
                if len(content_str) > LOG_TRUNCATE_LIMIT:
                    print(f"      {content_str[:LOG_TRUNCATE_LIMIT]}... [TRUNCATED]", flush=True)
                else:
                    indented = "\n".join([f"      {l}" for l in content_str.splitlines()])
                    print(indented, flush=True)
