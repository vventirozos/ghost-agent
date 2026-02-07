#!/usr/bin/env python3
import os
import sys
import json
import httpx
import asyncio
import datetime
import logging
import warnings
import argparse
import uuid
import hashlib
#import tiktoken
from transformers import AutoTokenizer
import contextvars
import socket
import urllib.parse
import urllib.request
import gc
import shutil
import importlib.util
import ast 
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks, Security, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

import uvicorn

import logging
import warnings

# --- ARGUMENTS ---
parser = argparse.ArgumentParser(description="Ghost Agent: Autonomous AI Service (Ollama Compatible)")
parser.add_argument("--host", default="0.0.0.0", help="Host interface")
parser.add_argument("--port", type=int, default=8000, help="Port")
parser.add_argument("--upstream-url", default="http://127.0.0.1:8080", help="Upstream LLM URL (Llama.cpp or Ollama)")
parser.add_argument("--temperature", "-t", type=float, default=0.7, help="LLM Sampling Temperature")
parser.add_argument("--daemon", "-d", action="store_true", help="Background mode")
parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
parser.add_argument("--no-memory", action="store_true", help="Disable Vector Memory to save RAM")
parser.add_argument("--max-context", type=int, default=8192, help="Max context window size in tokens")
parser.add_argument("--api-key", default=os.getenv("GHOST_API_KEY", "ghost-secret-123"), help="API Key for security")
parser.add_argument(
    "--smart-memory",
    type=float,
    default=0.0,
    help="Enable autonomous memory. 0.0 = Disabled. 0.1-1.0 = Selectivity Threshold."
)

# --- SEARCH PROVIDER OPTIONS ---
search_group = parser.add_mutually_exclusive_group()
search_group.add_argument(
    "--anonymous",
    action="store_true",
    default=True,
    help="Use Tor + DuckDuckGo for privacy (Default). Strict anonymity."
)
search_group.add_argument(
    "--public",
    action="store_false",
    dest="anonymous",
    help="Use Tavily Search (Public internet). Requires TAVILY_API_KEY env var."
)

logging.getLogger("transformers").setLevel(logging.ERROR)
args = parser.parse_args()

DEBUG_MODE = args.debug

# Validate Smart Memory Range
if not 0.0 <= args.smart_memory <= 1.0:
    print("Error: --smart-memory must be between 0.0 and 1.0")
    sys.exit(1)

# Validate Search Config
if not args.anonymous:
    if not os.getenv("TAVILY_API_KEY"):
        print("Error: --public mode requires 'TAVILY_API_KEY' environment variable.")
        sys.exit(1)
    if not importlib.util.find_spec("tavily"):
        print("Error: --public mode requires 'tavily' library. Install with: pip install tavily-python")
        sys.exit(1)

UPSTREAM_URL = args.upstream_url
if "://" not in UPSTREAM_URL:
    UPSTREAM_URL = f"http://{UPSTREAM_URL}"

request_id_context = contextvars.ContextVar("request_id", default="SYSTEM")
LOG_TRUNCATE_LIMIT = 300

# --- CONFIGURATION ---
BASE_DIR = Path(os.getenv("GHOST_HOME", Path.home() / "ghost_llamacpp"))
SANDBOX_DIR = BASE_DIR / "sandbox"
LOG_FILE = BASE_DIR / "system" / "ghost-agent.log"
PID_FILE = BASE_DIR / "system" / "ghost-agent.pid"
MEMORY_DIR = BASE_DIR / "system" / "memory"
LOCAL_TOKENIZER_PATH = BASE_DIR / "system" / "tokenizer"

GRANITE_MODEL_ID = "ibm-granite/granite-4.0-h-micro" 

DOCKER_IMAGE = "python:3.11-slim-bookworm"
CONTAINER_NAME = "ghost-agent-sandbox"
CONTAINER_WORKDIR = "/workspace"

# --- PERSISTENCE CONFIGURATION ---
# We use a single SQLite file for all structured system data (Jobs, Queues, etc.)
SQLITE_DB_PATH = MEMORY_DIR / "ghost.db"
# Ensure absolute path for SQLite
db_url = f"sqlite:///{SQLITE_DB_PATH.absolute()}"

jobstores = {
    'default': SQLAlchemyJobStore(url=db_url)
}

# Initialize Scheduler with the Persistent Store
SCHEDULE_LOG = BASE_DIR / "system" / "proactive_results.log"

scheduler = AsyncIOScheduler(jobstores=jobstores)

# --- PROMPTS (SPLIT ARCHITECTURE) ---

SYSTEM_PROMPT = """
### IDENTITY: Ghost (Autonomous Operations)
TIME: {{CURRENT_TIME}}

## CORE OBJECTIVE
You are a high-intelligence AI assistant capable of performing real-world tasks.

## TOOL SELECTION MAP (Use this strictly)
1.  **Fact-Checking & Verification (CRITICAL):**
    * **Trigger**: If the user asks to "fact-check", "verify", "debunk", or "confirm" a claim.
    * **Action**: You MUST call the `fact_check` tool.
    * **Rule**: Do NOT answer from memory. Even if the fact seems obvious (e.g., "Paris is in France"), you MUST run the tool to prove it.

2.  **Real-Time Facts (News, Dates, Prices):**
    * Action: Call `web_search`...

2.  **Complex Research (Summaries, "Learn about X", Deep Analysis):**
    * Action: Call `deep_research`.
    * Rule: Use this for broad topics requiring multiple sources.

3.  **Handling Files (PDFs, URLs, Documents):**
    * *To Learn/Read:* First `file_system(op='download'...)`, THEN `knowledge_base(action='ingest_document')`.
    * *Rule:* You cannot answer questions about a file until you have ingested it into memory.

4.  **Memory & Identity:**
    * *User Facts:* If the user says "I am [Name]" or "I live in [City]", call `update_profile`.
    * *Recall:* If the user asks "What did we discuss?" or "Do you remember X?", call `recall`.

## OPERATIONAL RULES
1.  **ACTION OVER SPEECH:** Do not say "I will checking the weather now." **JUST RUN THE TOOL.**
2.  **NO HALLUCINATIONS:** If a tool fails or returns an error, **REPORT THE ERROR**. Do NOT guess the weather (e.g. do not say "It is 22°C" if you don't know).
3.  **ADMINISTRATIVE LOCK:** Do NOT use `manage_tasks` unless explicitly asked to "schedule" or "automate".
4.  **PROFILE AWARENESS:** Always check the **USER PROFILE** (below) for context before searching.

### USER PROFILE
{{PROFILE}}
"""

###
###
###


FACT_CHECK_SYSTEM_PROMPT = """
### ROLE: LEAD INVESTIGATOR (Fact-Checking)
You are a rigorous verification engine. Your goal is to separate truth from fiction.

## TOOL STRATEGY (CRITICAL):
1. **Simple Facts** (Dates, Names, Capitals): Use `web_search`. It is fast and efficient.
2. **Complex Claims** (Politics, Science, "Did X happen?"): Use `deep_research`. You MUST read the full source text to verify context and avoid snippet hallucinations.
3. **Internal Knowledge**: Use `recall` first to check if we have established facts in memory.

## EXECUTION STEPS:
1. **DECOMPOSE**: Break the user's request into individual atomic claims.
2. **VERIFY**: For each claim, select the correct tool (`web_search` vs `deep_research`) based on complexity.
3. **ANALYZE**: Cross-reference the evidence. If sources conflict, search for a "tie-breaker" (official government or academic source).
4. **REPORT**: Output the final verdict.

## OUTPUT FORMAT:
- **Claim**: <The specific statement>
- **Verdict**: [TRUE / FALSE / MISLEADING / UNVERIFIABLE]
- **Confidence**: <0-100%>
- **Tool Used**: <Which tool you chose and why>
- **Evidence**: <Concise summary of what the source actually said>
- **Source**: <URL>
"""


###
###
###


CODE_SYSTEM_PROMPT = r"""
### 🐍 SYSTEM PROMPT: PYTHON SPECIALIST (LINUX)

**ROLE:**
You are **Ghost**, an expert Python Data Engineer and Linux Operator.
You are capable of performing multi-step tasks involving file manipulation, research, and coding.

**🚫 OPERATIONAL RULES**
1.  **EXECUTION:** When asked to write code, output **RAW, EXECUTABLE PYTHON CODE**. Do not use Markdown blocks (```python) inside the `execute` tool content, but YOU MAY use Markdown in your normal explanations.
2.  **TOOLS FIRST:** If the user asks for data you don't have, use `web_search`, `file_system`, or `knowledge_base` *before* you write the script.
3.  **ROBUSTNESS:** If a file might not exist, use `os.path.exists` or `try/except`.
4.  **VERIFICATION:** After running a script, analyze the output. If it fails, fix it.

**🧠 CODING GUIDELINES**
1.  **VISIBILITY:** You MUST use `print(...)` to show results. If you calculate something but don't print it, the user sees nothing.
2.  **IMPORTS:** Always import necessary libraries (e.g., `import os`, `import pandas as pd`) at the top.
3.  **ROBUSTNESS:** If a file might not exist, use `os.path.exists` or `try/except`.
4.  **NAMING:** Use snake_case for variables.

**GOAL:**
Complete the user's objective efficiently. If it requires downloading > learning > coding, perform them in order.
"""




#####
#####
#####
#####
SMART_MEMORY_PROMPT = """
You are a Memory Filter. Your goal is to extract important information from the conversation.

SCORING GUIDE:
- 1.0: CRITICAL Identity Facts (Name, Location, Job, Core Tech Stack, "I am..."). -> TRIGGERS PROFILE UPDATE.
- 0.8: Project Context (Current error, file path being edited, specific library versions).
- 0.1: General Knowledge / Chit-Chat. -> DISCARD.

FORMAT:
Return ONLY a JSON object.
If the Score is 0.9 or higher, you MUST provide the "profile_update" structure.
{
  "score": <float>,
  "fact": "<concise string summary>",
  "profile_update": {
      "category": "<root|projects|notes|relationships>",
      "key": "<specific label>",
      "value": "<content>"
  } (OR null if score < 0.9)
}
"""

TOR_PROXY = os.getenv("TOR_PROXY", "socks5://127.0.0.1:9050")

SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# --- LOGGING SETUP ---
logger = logging.getLogger("GhostAgent")
logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')

fh = logging.FileHandler(LOG_FILE)
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)
logger.addHandler(fh)

if not args.daemon:
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    sh.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
    logger.addHandler(sh)

for lib in ["httpx", "uvicorn", "docker", "chromadb", "urllib3", "pypdf"]:
    logging.getLogger(lib).setLevel(logging.WARNING)

http_client: Optional[httpx.AsyncClient] = None
sandbox_manager = None

class Icons:
    # Lifecycle
    BOOT_START   = "🔆"  # System starting
    BOOT_DOCKER  = "🐳"  # Docker spinning up
    BOOT_MEMORY  = "💿"  # Vector DB loading
    BOOT_READY   = "🚀"  # Fully ready

    # Request Flow
    REQ_QUEUE    = "⏳"  # Request received
    REQ_START    = "🎬"  # Processing start
    REQ_END      = "🏁"  # Processing done

    # Intelligence
    CTX_LOAD     = "🧩"  # Context/Memory injected
    LLM_ASK      = "🗣️"  # Sending to LLM
    LLM_REPLY    = "🤖"  # Response from LLM

    # Memory Operations
    MEM_SMART    = "✨"  # Smart extraction
    MEM_SAVE     = "💾"  # Writing to DB
    MEM_SEARCH   = "🔍"  # Reading from DB
    MEM_WIPE     = "🧹"  # Forgetting

    # Tools
    TOOL_CALL    = "🛠️"  # Tool invoked
    TOOL_OK      = "✅"  # Tool success
    TOOL_FAIL    = "💥"  # Tool failure
    TOOL_FILE    = "📂"  # File operations
    TOOL_NET     = "🌐"  # Network/Search operations

    # System
    SYS_HEALTH   = "🏥"  # Health check
    SYS_INFO     = "ℹ️"   # General info

class ProfileMemory:
    def __init__(self, path: Path):
        self.file_path = path / "user_profile.json"
        if not self.file_path.exists():
            self.save({"root": {"name": "User"}, "relationships": {}, "interests": {}, "assets": {}})

    def load(self) -> Dict[str, Any]:
        try: 
            return json.loads(self.file_path.read_text())
        except: 
            return {"root": {"name": "User"}, "relationships": {}, "interests": {}, "assets": {}}

    def save(self, data: Dict[str, Any]):
        self.file_path.write_text(json.dumps(data, indent=2))

    def update(self, category: str, key: str, value: Any):
        data = self.load()
        cat = str(category).strip().lower()
        k = str(key).strip().lower()
        v = str(value).strip()

        # --- STRICT MAPPING (Prevents Duplicates) ---
        mapping = {
            "wife": ("relationships", "wife"),
            "husband": ("relationships", "husband"),
            "son": ("relationships", "son"),
            "daughter": ("relationships", "daughter"),
            "car": ("assets", "car"),
            "vehicle": ("assets", "car"),
            "science": ("interests", "science"),
            "interest": ("interests", "general")
        }

        if k in mapping:
            cat, target_key = mapping[k]
        else:
            target_key = k

        # Ensure category exists as a dictionary
        if cat not in data or not isinstance(data[cat], dict):
            data[cat] = {}

        data[cat][target_key] = v
        self.save(data)
        return f"Synchronized: {cat}.{target_key} = {v}"

    def get_context_string(self) -> str:
        data = self.load()
        lines = []
        for key, val in data.items():
            if not val: continue
            label = key.replace("_", " ").capitalize()
            if isinstance(val, dict):
                lines.append(f"## {label}:")
                for sub_k, sub_v in val.items():
                    lines.append(f"- {sub_k}: {sub_v}")
            elif isinstance(val, list):
                lines.append(f"## {label}: " + ", ".join([str(i) for i in val]))
            else:
                lines.append(f"{label}: {val}")
        return "\n".join(lines)

# --- HELPER: UTC TIMESTAMP ---
def get_utc_timestamp():
    """Returns strict ISO8601 UTC timestamp for Go/iOS clients."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def pretty_log(title: str, content: Any = None, icon: str = "📝", level: str = "INFO", special_marker: str = None):
    req_id = request_id_context.get()

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if special_marker == "BEGIN":
        print(f"[{level}] 🎬 {timestamp} - [{req_id}] -- REQUEST STARTED", flush=True)
        return
    if special_marker == "END":
        print(f"[{level}] 🏁 {timestamp} - [{req_id}] --", flush=True)
        return

    log_line = f"[{level}] {icon} {timestamp} - [{req_id}] {title.upper()}"
    print(log_line, flush=True)

    if content is not None:
        content_str = ""
        if isinstance(content, (dict, list)):
            try: content_str = json.dumps(content, indent=2, default=str)
            except: content_str = str(content)
        else:
            content_str = str(content)

        logger.debug(f"DETAILS FOR [{req_id}] {title}: {content_str}")

        if level == "ERROR" or DEBUG_MODE:
            if len(content_str) > LOG_TRUNCATE_LIMIT:
                 print(f"{content_str[:LOG_TRUNCATE_LIMIT]}... [TRUNCATED]", flush=True)
            else:
                 print(f"{content_str}", flush=True)

def recursive_split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 70) -> List[str]:
    if not text: return []
    if len(text) <= chunk_size: return [text]
    
    separators = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""]
    final_chunks = []
    stack = [text]
    
    while stack:
        current_text = stack.pop()
        
        if len(current_text) <= chunk_size:
            final_chunks.append(current_text)
            continue
            
        found_sep = ""
        for sep in separators:
            if sep in current_text:
                found_sep = sep
                break
        
        if not found_sep:
            for i in range(0, len(current_text), chunk_size - chunk_overlap):
                final_chunks.append(current_text[i:i+chunk_size])
            continue
            
        parts = current_text.split(found_sep)
        buffer = ""
        temp_chunks = []
        
        for p in parts:
            fragment = p + found_sep if found_sep.strip() else p
            if len(buffer) + len(fragment) <= chunk_size:
                buffer += fragment
            else:
                if buffer:
                    temp_chunks.append(buffer.strip())
                buffer = fragment
        
        if buffer:
            temp_chunks.append(buffer.strip())

        for chunk in reversed(temp_chunks):
            if len(chunk) > chunk_size:
                if found_sep == "":
                    for i in range(0, len(chunk), chunk_size - chunk_overlap):
                        final_chunks.append(chunk[i:i+chunk_size])
                else:
                    stack.append(chunk) 
            else:
                final_chunks.append(chunk)

    return final_chunks

async def helper_fetch_url_content(url: str) -> str:
    # 1. Setup Tor Proxy
    proxy_url = os.getenv("TOR_PROXY", "socks5://127.0.0.1:9050")
    if proxy_url.startswith("socks5://"): 
        proxy_url = proxy_url.replace("socks5://", "socks5h://")

    try:
        # 2. Inject Proxy into Client
        async with httpx.AsyncClient(proxy=proxy_url, timeout=15.0, follow_redirects=True) as client:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, 'html.parser')
            for script in soup(["script", "style", "nav", "footer", "iframe", "svg"]):
                script.decompose()
            
            text = soup.get_text(separator=' ', strip=True)
            text = " ".join(text.split())
            return text
            
    except Exception as e:
        return f"Error reading {url}: {str(e)}"


class DockerSandbox:
    def __init__(self, host_workspace: Path):
        try:
            import docker
            from docker.errors import NotFound, APIError
            self.docker_lib = docker
            self.NotFound = NotFound
            self.APIError = APIError
        except ImportError:
            logger.error("Docker library not found. pip install docker")
            raise

        self.client = self.docker_lib.from_env()
        self.host_workspace = host_workspace.absolute()
        self.container = None
        self.image = "python:3.11-slim-bookworm"

        pretty_log("Sandbox Init", f"Mounting {self.host_workspace} -> {CONTAINER_WORKDIR}", icon=Icons.BOOT_DOCKER)

    def get_stats(self):
        if not self.container: return None
        try: return self.container.stats(stream=False)
        except: return None

    def _is_container_ready(self):
        try:
            self.container.reload()
            return self.container.status == "running"
        except:
            return False

    def ensure_running(self):
        import time
        try:
            if not self.container:
                self.container = self.client.containers.get(CONTAINER_NAME)
        except self.NotFound:
            pass 

        # 1. Start Container (SUPERCHARGED CONFIG)
        if not (self.container and self._is_container_ready()):
            pretty_log("Sandbox", "Initializing High-Performance Environment...", icon="⚙️")
            try:
                try:
                    old = self.client.containers.get(CONTAINER_NAME)
                    old.remove(force=True)
                    time.sleep(1) 
                except self.NotFound: pass

                self.container = self.client.containers.run(
                    self.image,
                    command="sleep infinity",
                    name=CONTAINER_NAME,
                    detach=True,
                    tty=True,
                    volumes={str(self.host_workspace): {'bind': CONTAINER_WORKDIR, 'mode': 'rw'}},
                    mem_limit="512m", 
                    network_mode="bridge",
                )
                
                for _ in range(10):
                    if self._is_container_ready(): break
                    time.sleep(1)
                
            except Exception as e:
                pretty_log("Sandbox Error", f"Failed to start: {e}", level="ERROR")
                raise e

        # 2. Install Full Data Science Stack
        # We use a marker file (.supercharged) to avoid reinstalling on every boot
        exit_code, _ = self.container.exec_run("test -f /root/.supercharged")
        if exit_code != 0:
            pretty_log("Sandbox", "Installing Deep Learning Stack (Wait ~60s)...", icon="📦")
            
            # A. System Dependencies
            self.container.exec_run("apt-get update && apt-get install -y coreutils nodejs npm g++ curl wget git procps")
            
            # B. Python ML Stack (Torch, Scikit, Pandas, etc.)
            # Note: We use --no-cache-dir to keep the layer size down
            install_cmd = (
                "pip install --no-cache-dir "
                "torch numpy pandas scipy matplotlib seaborn "
                "scikit-learn yfinance beautifulsoup4 networkx requests "
                "pylint black mypy bandit"
            )
            self.container.exec_run(install_cmd)
            
            # C. Mark as done
            self.container.exec_run("touch /root/.supercharged")
            pretty_log("Sandbox", "Environment Ready & Supercharged (2GB RAM).", icon="✅")

    def execute(self, cmd: str, timeout: int = 300):
        try:
            self.ensure_running()
            if not self._is_container_ready():
                return "Error: Container refused to start.", 1

            cmd_string = f"timeout {timeout}s {cmd}"
            
            # --- FIX: EXECUTE AS HOST USER ---
            # This ensures files created inside the container are owned by 'vasilis' (you),
            # preventing the "root root" permission lock issue.
            user_id = os.getuid()
            group_id = os.getgid()
            
            exec_result = self.container.exec_run(
                cmd_string, 
                workdir=CONTAINER_WORKDIR, 
                demux=True,
                user=f"{user_id}:{group_id}" 
            )
            
            stdout_bytes, stderr_bytes = exec_result.output
            exit_code = exec_result.exit_code

            output = ""
            if stdout_bytes: output += stdout_bytes.decode("utf-8", errors="replace")
            if stderr_bytes: 
                if output: output += "\n--- STDERR ---\n"
                output += stderr_bytes.decode("utf-8", errors="replace")

            if not output.strip() and exit_code != 0:
                 output = f"[SYSTEM ERROR]: Process failed (Exit {exit_code}) with no output."

            return output, exit_code

        except Exception as e:
            return f"Container Execution Error: {str(e)}", 1

class VectorMemory:
    def __init__(self, *args, **kwargs):
        """
        Robust Initialization with Explicit Settings.
        Accepts *args and **kwargs to prevent instantiation errors.
        """
        import sys
        from chromadb.config import Settings
        
        # 1. SETUP PATHS (Define this FIRST)
        self.chroma_dir = MEMORY_DIR 
        if not self.chroma_dir.exists():
            self.chroma_dir.mkdir(parents=True, exist_ok=True)

        # --- FIX: DEFINE LIBRARY FILE TRACKER ---
        self.library_file = self.chroma_dir / "library_index.json"
        if not self.library_file.exists():
            self.library_file.write_text("[]")
        # ----------------------------------------

        # 2. SETUP EMBEDDINGS
        try:
            # Re-import locally to ensure context is clear
            from chromadb.utils import embedding_functions
            self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
        except Exception as e:
            print(f"Error loading embedding model: {e}")
            sys.exit(1)

        # 3. CONNECT TO DATABASE
        try:
            self.client = chromadb.PersistentClient(
                path=str(self.chroma_dir),
                settings=Settings(
                    allow_reset=True,
                    anonymized_telemetry=False
                )
            )
            
            # 4. GET COLLECTION (Force name 'agent_memory')
            self.collection = self.client.get_or_create_collection(
                name="agent_memory",
                embedding_function=self.embedding_fn
            )
            
            pretty_log("Memory System", f"Initialized. Total Records: {self.collection.count()}", icon="🧠")
            
        except Exception as e:
            if "already exists" in str(e):
                print(f"\n[FATAL] DATABASE LOCKED: {e}")
                print("👉 ACTION REQUIRED: Run 'pkill -9 -f python' in your terminal.\n")
                sys.exit(1)
            print(f"CRITICAL DB ERROR: {e}")
            self.collection = None

    def search_advanced(self, query: str, limit: int = 5):
        """
        Returns detailed search results with scores and metadata.
        Required for the new 'Recall' tool to filter hallucinations.
        """
        results = self.collection.query(
            query_texts=[query],
            n_results=limit
        )
        
        parsed_results = []
        if results['ids']:
            for i in range(len(results['ids'][0])):
                parsed_results.append({
                    "id": results['ids'][0][i],
                    "text": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i],
                    "score": results['distances'][0][i]
                })
        
        return parsed_results

    def _update_library_index(self, filename: str, action: str):
        try:
            data = json.loads(self.library_file.read_text())
            if action == "add" and filename not in data:
                data.append(filename)
            elif action == "remove" and filename in data:
                data.remove(filename)
            self.library_file.write_text(json.dumps(data))
        except Exception as e:
            logger.error(f"Library index error: {e}")

    def get_library(self):
        """
        Returns the list of known documents.
        SAFE: Returns [] if index is missing or empty.
        """
        index_path = self.chroma_dir / "library_index.json"
        
        if not index_path.exists():
            return []  # Return empty list, not None
            
        try:
            with open(index_path, "r") as f:
                data = json.load(f)
                # Ensure it's actually a list
                if isinstance(data, list):
                    return data
                return []
        except Exception:
            # If JSON is corrupt, return empty and self-heal later
            return []
    
    def add(self, text: str, meta: dict = None):
        if len(text) < 5: return
        mem_id = hashlib.md5(text.encode("utf-8")).hexdigest()

        existing = self.collection.get(ids=[mem_id])
        if existing and existing['ids']:
            return

        metadata = meta or {"timestamp": get_utc_timestamp(), "type": "auto"}
        self.collection.add(documents=[text], metadatas=[metadata], ids=[mem_id])
        pretty_log("Memory Stored", text[:100], icon=Icons.MEM_SAVE)

    def smart_update(self, text: str, type_label: str = "auto"):
        try:
            results = self.collection.query(query_texts=[text], n_results=1)
            if results['ids'] and results['ids'][0]:
                dist = results['distances'][0][0]
                existing_id = results['ids'][0][0]

                if dist < 0.5:
                    self.collection.delete(ids=[existing_id])
                    pretty_log("Smart Memory", "Overwriting Old Memory", icon="♻️")

            self.add(text, {"timestamp": get_utc_timestamp(), "type": type_label})
        except Exception as e:
            logger.error(f"Smart Update Error: {e}")

    def ingest_document(self, filename: str, chunks: List[str]):
        try:
            ids = [hashlib.md5(f"{filename}_{i}_{chunk[:20]}".encode()).hexdigest() for i, chunk in enumerate(chunks)]
            metadatas = [{"timestamp": get_utc_timestamp(), "type": "document", "source": filename} for _ in range(len(chunks))]

            batch_size = 20

            total_batches = (len(chunks) + batch_size - 1) // batch_size
            for i in range(0, len(chunks), batch_size):
                self.collection.upsert(
                    documents=chunks[i:i + batch_size],
                    metadatas=metadatas[i:i + batch_size],
                    ids=ids[i:i + batch_size]
                )
                if i % 10 == 0:
                    pretty_log(f"Ingesting {filename}", f"Chunk {i+1}/{len(chunks)}", icon="📚")

            self._update_library_index(filename, "add")
            return True, f"Successfully ingested {len(chunks)} chunks from {filename}."
        except Exception as e:
            logger.error(f"Ingest failed: {e}")
            return False, str(e)

    def search(self, query: str, inject_identity: bool = True):
            try:
                search_queries = [query]
                if inject_identity:
                    search_queries.insert(0, "User's profile. User's name. User preferences.")

                results = self.collection.query(
                    query_texts=search_queries,
                    n_results=10,
                )

                candidates = []
                seen_docs = set()

                def process_batch(batch_idx, is_identity_batch):
                    if not results['documents'] or len(results['documents']) <= batch_idx:
                        return

                    for doc, meta, dist in zip(
                        results['documents'][batch_idx],
                        results['metadatas'][batch_idx],
                        results['distances'][batch_idx]
                    ):
                        if doc in seen_docs: continue

                        m_type = meta.get('type', 'auto')
                        doc_lower = doc.lower()
                        timestamp = meta.get('timestamp', '0000-00-00')

                        is_summary = m_type == "document_summary"

                        is_name_memory = (
                            "name is" in doc_lower or
                            "call me" in doc_lower or
                            "user's" in doc_lower or
                            "user is" in doc_lower
                        )

                        if is_name_memory:
                            threshold = 1.2 
                        elif is_summary:
                            threshold = 0.85
                        elif is_identity_batch:
                            threshold = 0.9 if m_type == 'manual' else 0.75
                        else:
                            threshold = 0.85 if m_type == 'manual' else 0.70

                        if dist < threshold or is_name_memory or is_summary:
                            priority_score = 1

                            if is_name_memory: priority_score = -20
                            elif is_summary: priority_score = -15
                            elif is_identity_batch: priority_score = -10
                            elif m_type == 'manual': priority_score = 0
                            elif m_type == 'document': priority_score = 2

                            candidates.append({
                                "doc": doc,
                                "meta": meta,
                                "dist": dist,
                                "type": m_type,
                                "p_score": priority_score,
                                "timestamp": timestamp
                            })
                            seen_docs.add(doc)

                if inject_identity:
                    process_batch(0, is_identity_batch=True)
                    process_batch(1, is_identity_batch=False)
                else:
                    process_batch(0, is_identity_batch=False)

                candidates.sort(key=lambda x: (x['p_score'], x['timestamp']), reverse=False)

                final_selection = candidates[:6]
                if not final_selection: return ""

                output = []
                for item in final_selection:
                    ts = item['meta'].get('timestamp', '?')
                    m_type = item['meta'].get('type', 'auto').upper()
                    doc_text = item['doc']

                    prefix = ""
                    if item['p_score'] <= -15: prefix = "**[MASTER SUMMARY]** "
                    elif item['p_score'] <= -10: prefix = "**[IDENTITY]** "
                    elif item['p_score'] == 0: prefix = "**[USER PRIORITY]** "
                    elif item['p_score'] == 2: prefix = "**[DOCUMENT SOURCE]** "

                    output.append(f"[{ts}] ({m_type}) {prefix}{doc_text}")

                return "\n---\n".join(output)

            except Exception as e:
                logger.error(f"Search failed: {e}")
                return ""

    def delete_document_by_name(self, filename: str):
        # DIRECT DB DELETE
        self.collection.delete(where={"source": filename})
        
        # Cleanup Index (Secondary)
        idx = self.chroma_dir / "library_index.json"
        try:
            data = json.loads(idx.read_text())
            if filename in data:
                data.remove(filename)
                idx.write_text(json.dumps(data))
        except: pass
        
        return True, "Deleted"

    def delete_by_query(self, query: str):
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=1,
                where={"type": {"$ne": "document"}}
            )
            if not results['ids'] or not results['ids'][0]:
                return False, "Memory not found. (Note: Use 'forget_document' to delete learned books)."

            dist = results['distances'][0][0]
            doc_text = results['documents'][0][0]
            mem_id = results['ids'][0][0]

            if dist > 0.5:
                return False, f"Best match was '{doc_text}' but score ({dist:.2f}) was too low. Be more specific."

            self.collection.delete(ids=[mem_id])
            pretty_log("Memory Deleted", doc_text, icon="🗑️")
            return True, f"Successfully forgot: [[{doc_text}]]"
        except Exception as e:
            return False, f"Error: {e}"

# --- CONTEXT MANAGEMENT LOGIC ---

# ... inside agent.granite4.py ...

# --- TOKENIZER CONFIGURATION ---
# We prioritize a local "frozen" tokenizer for offline stability.
# If missing, we attempt to download it via Tor (risky but necessary fallback).


def load_tokenizer():
    """
    Robust loading strategy: LOCAL DISK -> TOR NETWORK -> FALLBACK
    """
    # 1. Try Local Disk (Offline Mode) - PREFERRED
    if LOCAL_TOKENIZER_PATH.exists() and (LOCAL_TOKENIZER_PATH / "tokenizer.json").exists():
        try:
            print(f"📂 Loading Tokenizer from local cache: {LOCAL_TOKENIZER_PATH}")
            return AutoTokenizer.from_pretrained(str(LOCAL_TOKENIZER_PATH), local_files_only=True)
        except Exception as e:
            print(f"⚠️ Local tokenizer corrupted: {e}")

    # 2. Try Network Download (Tor Mode) - FALLBACK
    print(f"⏳ Local missing. Downloading {GRANITE_MODEL_ID} via Tor...")
    
    # Force Remote DNS (socks5h) to prevent leaks and 'Host not found' errors
    tor_proxy = os.getenv("TOR_PROXY", "socks5h://127.0.0.1:9050")
    if tor_proxy.startswith("socks5://"):
        tor_proxy = tor_proxy.replace("socks5://", "socks5h://")
        
    try:
        # We pass proxies explicitly to override any confusing environment vars
        tokenizer = AutoTokenizer.from_pretrained(
            GRANITE_MODEL_ID,
            proxies={"http": tor_proxy, "https": tor_proxy}
        )
        
        # Save it immediately so we never have to download again
        print(f"💾 Caching tokenizer to {LOCAL_TOKENIZER_PATH}...")
        tokenizer.save_pretrained(str(LOCAL_TOKENIZER_PATH))
        return tokenizer
        
    except Exception as e:
        print(f"❌ Network download failed: {e}")
        return None

# --- INITIALIZATION ---
TOKEN_ENCODER = load_tokenizer()

if TOKEN_ENCODER:
    print("✅ Granite Tokenizer Online.")
else:
    print("⚠️  OPERATING IN DEGRADED MODE: Using character estimation (Context overflow risk).")

# --- UPDATED ESTIMATOR FUNCTION ---
def estimate_tokens(text: str) -> int:
    if not text: return 0
    if TOKEN_ENCODER:
        try:
            return len(TOKEN_ENCODER.encode(text))
        except: pass
    return len(text) // 3  # Crude fallback for Granite


# --- REPLACES: def estimate_tokens(text: str) -> int: ---

def estimate_tokens(text: str) -> int:
    """
    Accurately estimates tokens using the Granite tokenizer.
    Falls back to character approximation if the tokenizer failed to load.
    """
    if not text:
        return 0
        
    # CASE 1: High-Accuracy Granite Tokenizer
    if TOKEN_ENCODER:
        try:
            # Transformers returns a list of input_ids; we just need the count
            return len(TOKEN_ENCODER.encode(text))
        except Exception:
            # Fallback for encoding errors (rare encoding artifacts)
            return len(text) // 3
            
    # CASE 2: Fallback (No tokenizer loaded)
    # Granite models generally average ~3-4 characters per token
    return len(text) // 3

def process_rolling_window(messages: List[Dict[str, Any]], max_tokens: int) -> List[Dict[str, Any]]:
    if not messages: return []

    system_msgs = [m for m in messages if m.get("role") == "system"]
    raw_history = [m for m in messages if m.get("role") != "system"]

    clean_history = []
    seen_tool_calls = set()

    for msg in raw_history:
        role = msg.get("role")
        content = str(msg.get("content", ""))

        if role == "tool":
            tool_name = msg.get('name', 'unknown')
            call_id = f"{tool_name}:{content[:100]}"
            if call_id in seen_tool_calls: continue
            seen_tool_calls.add(call_id)

        if role == "assistant":
            lower_content = content.lower()
            if "memory has been updated" in lower_content or "memory stored" in lower_content:
                continue

        clean_history.append(msg)

    current_tokens = sum(estimate_tokens(m.get("content", "")) for m in system_msgs)
    final_history = []

    for msg in reversed(clean_history):
        msg_tokens = estimate_tokens(msg.get("content", ""))
        if current_tokens + msg_tokens > max_tokens:
            break
        final_history.append(msg)
        current_tokens += msg_tokens

    final_history.reverse()
    return system_msgs + final_history

async def run_smart_memory_task(interaction_context: str, model_name: str, selectivity: float):
    if not memory_system: return

    final_prompt = SMART_MEMORY_PROMPT + f"\n{interaction_context}"

    try:
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": final_prompt}],
            "stream": False,
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }

        resp = await http_client.post("/v1/chat/completions", json=payload)
        if resp.status_code != 200: return

        data = resp.json()
        if not data.get("choices"): return
        content = data["choices"][0]["message"]["content"]

        try:
            import json
            clean_content = content.replace("```json", "").replace("```", "").strip()
            result_json = json.loads(clean_content)

            score = float(result_json.get("score", 0.0))
            fact = result_json.get("fact", "")
            profile_up = result_json.get("profile_update", None)

        except Exception:
            return

        is_high_value = (score >= 0.9 and profile_up is not None)
        memory_type = "identity" if is_high_value else "auto"

        if score >= selectivity and fact:
            if len(fact) > 200: return
            if len(fact) < 5 or "none" in fact.lower(): return

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, memory_system.smart_update, fact, memory_type)
            
            pretty_log(
                "Vector Memory Write", 
                f"[{score}] Stored as '{memory_type}': {fact}", 
                icon=Icons.MEM_SAVE
            )

            if is_high_value and profile_memory:
                try:
                    cat = profile_up.get("category", "notes")
                    key = profile_up.get("key", "info")
                    val = profile_up.get("value", fact)

                    msg = profile_memory.update(cat, key, val)
                    
                    pretty_log(
                        "Profile Memory Write", 
                        f"Updated {cat}.{key} -> {val}", 
                        icon="👤"
                    )
                    
                except Exception as e:
                    logger.error(f"Auto-Profile failed: {e}")

    except Exception as e:
        logger.error(f"Smart memory task failed: {e}")

# --- INTERNAL HELPERS (RETAINED FOR NEW TOOLS) ---

async def tool_fact_check(statement: str):
    pretty_log("Tool Call: Deep Fact Check", statement, icon="🕵️")
    
    allowed_names = ["deep_research"]
    restricted_tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in allowed_names]
    
    # ... (Keep strict prompt and payload setup the same) ...
    STRICT_PROMPT = """
    ### ROLE: DEEP FORENSIC VERIFIER
    You are a high-precision fact-checker. 
    
    ## CRITICAL RULES:
    1. **MANDATORY VERIFICATION**: You MUST call `deep_research` to verify this claim.
    2. **NO INTERNAL KNOWLEDGE**: Do not answer from memory.
    3. **OUTPUT**: Once you have the search results, summarize them as the final answer.
    """

    messages = [
        {"role": "system", "content": STRICT_PROMPT},
        {"role": "user", "content": f"Verify this claim with evidence: {statement}"}
    ]

    payload = {
        "model": "ghost-agent",
        "messages": messages,
        "tools": restricted_tools,
        "tool_choice": "required"
    }

    try:
        # Turn 1: Get Tool Call
        resp = await http_client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls:
            return "Error: Sub-agent failed to initiate Deep Research."

        # Turn 2: Execute Tool
        call = tool_calls[0]
        func_name = call["function"]["name"]
        func_args = json.loads(call["function"]["arguments"])
        
        if func_name == "deep_research":
            research_result = await tool_deep_research(**func_args)
            
            # Turn 3: Synthesize
            messages.append(msg)
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": func_name,
                "content": str(research_result)
            })
            
            payload["tool_choice"] = "none"
            payload["messages"] = messages
            
            final_resp = await http_client.post("/v1/chat/completions", json=payload)
            final_resp.raise_for_status()
            content = final_resp.json()["choices"][0]["message"]["content"]
            
            # --- THE FIX: WRAP THE OUTPUT ---
            # We prepend a system instruction to force the Main Agent to stop looping.
            return (
                f"SYSTEM INSTRUCTION: Verification Complete. STOP searching. "
                f"Present the following findings to the user immediately.\n\n"
                f"--- FINDINGS ---\n{content}"
            )

        return f"Error: Unauthorized tool '{func_name}'"

    except Exception as e:
        return f"Deep check critical failure: {e}"

async def tool_get_current_time():
    pretty_log("Tool Call: Get Time", None, icon="⌚")
    now = datetime.datetime.now()
    # Returns: "2024-05-21 14:30:05 (Tuesday)"
    return f"Current System Time: {now.strftime('%Y-%m-%d %H:%M:%S')} (Day: {now.strftime('%A')})"

async def tool_get_weather(location: str = None):
    """
    Robust Weather Fetcher (Open-Meteo -> wttr.in Fallback).
    Anonymous via Tor. Auto-detects profile location if missing.
    """
    # --- 1. SMART LOCATION FALLBACK ---
    if not location and profile_memory:
        try:
            data = profile_memory.load()
            found_loc = (
                data.get("root", {}).get("location") or 
                data.get("root", {}).get("city") or 
                data.get("personal", {}).get("location")
            )
            if found_loc:
                location = found_loc
                pretty_log("Weather", f"Using profile location: {location}", icon="📍")
        except: pass

    pretty_log("Tool Call: Get Weather", f"Target: {location}", icon="🌦️")
    
    if not location:
        return "SYSTEM ERROR: No location provided. You MUST specify a city (e.g., 'London') or update your profile."

    # --- 2. PROXY CONFIGURATION ---
    proxy_url = TOR_PROXY
    if proxy_url.startswith("socks5://"):
        proxy_url = proxy_url.replace("socks5://", "socks5h://")
    
    # --- 3. PROVIDER 1: OPEN-METEO ---
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=20.0) as client:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
            geo_resp = await client.get(geo_url)
            
            if geo_resp.status_code == 200 and geo_resp.json().get("results"):
                res = geo_resp.json()["results"][0]
                lat, lon, name = res["latitude"], res["longitude"], res["name"]
                
                w_url = (
                    f"https://api.open-meteo.com/v1/forecast?"
                    f"latitude={lat}&longitude={lon}&"
                    f"current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&"
                    f"wind_speed_unit=kmh"
                )
                w_resp = await client.get(w_url)
                
                if w_resp.status_code == 200:
                    curr = w_resp.json().get("current", {})
                    wmo_map = {0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast", 45: "Fog", 61: "Rain", 63: "Heavy Rain", 71: "Snow", 95: "Thunderstorm"}
                    cond = wmo_map.get(curr.get("weather_code"), "Variable")
                    
                    return (
                        f"REPORT (Source: Open-Meteo): Weather in {name}\n"
                        f"Condition: {cond}\n"
                        f"Temp: {curr.get('temperature_2m')}°C\n"
                        f"Wind: {curr.get('wind_speed_10m')} km/h\n"
                        f"Humidity: {curr.get('relative_humidity_2m')}%"
                    )
    except Exception as e:
        pretty_log("Open-Meteo Failed", f"{e} -> Trying Fallback...", level="WARN")

    # --- 4. PROVIDER 2: WTTR.IN (FALLBACK) ---
    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=3"
        async with httpx.AsyncClient(proxy=proxy_url, timeout=20.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and "<html" not in resp.text.lower():
                return f"REPORT (Source: wttr.in): {resp.text.strip()}"
    except Exception as e:
        pretty_log("Wttr.in Failed", str(e), level="ERROR")

    return "SYSTEM ERROR: Connection failed to all weather providers via Tor."

async def tool_system_utility(action: str, location: str = None):
    """Consolidated system operations."""
    if action == "check_time":
        return await tool_get_current_time()
    elif action == "check_health":
        return await tool_system_health()
    elif action == "check_location":
        return await tool_get_user_location()
    elif action == "check_weather":
        # Route to the robust, anonymous Tor-based function
        return await tool_get_weather(location)
    else:
        return f"Error: Unknown action '{action}'"

async def tool_schedule_task(task_name: str, prompt: str, cron_expression: str):
    pretty_log("Tool Call: Schedule Task", f"Name: {task_name} | Expr: {cron_expression}", icon="📆")
    try:
        job_id = f"task_{hashlib.md5(task_name.encode()).hexdigest()[:6]}"
        
        if cron_expression.startswith("interval:"):
            parts = cron_expression.split(":")
            if len(parts) > 1:
                secs = int(parts[1].strip())
            else:
                secs = 60 
            
            scheduler.add_job(
                run_proactive_task, 
                'interval', 
                seconds=secs, 
                args=[job_id, prompt], 
                id=job_id,
                name=task_name,
                replace_existing=True
            )
        else:
            scheduler.add_job(
                run_proactive_task, 
                CronTrigger.from_crontab(cron_expression), 
                args=[job_id, prompt], 
                id=job_id,
                name=task_name,
                replace_existing=True
            )
            
        memory_entry = f"Scheduled task '{task_name}' is running with ID {job_id} on schedule {cron_expression}."
        if memory_system:
            await asyncio.to_thread(memory_system.add, memory_entry, {"type": "manual", "task_id": job_id})
            
        return f"SUCCESS: Task '{task_name}' scheduled (ID: {job_id})."
    except Exception as e:
        pretty_log("Schedule Error", str(e), level="ERROR")
        return f"ERROR: {e}"

async def tool_stop_all_tasks():
    pretty_log("Tool Call: Stop All Tasks", "Deleting all jobs...", icon="🛑")
    try:
        jobs = scheduler.get_jobs()
        if not jobs:
            return "No active tasks to stop."
        
        count = len(jobs)
        scheduler.remove_all_jobs()
        
        return f"SUCCESS: Stopped and removed {count} scheduled tasks."
    except Exception as e:
        return f"Error stopping tasks: {e}"

async def tool_stop_task(task_identifier: str):
    pretty_log("Tool Call: Stop Task", task_identifier, icon="🛑")
    
    jobs = scheduler.get_jobs()
    target_job = None
    
    for job in jobs:
        if job.id == task_identifier or (hasattr(job, 'name') and job.name == task_identifier):
            target_job = job
            break
    
    if not target_job:
        return f"Error: No active task found matching '{task_identifier}'."

    try:
        scheduler.remove_job(target_job.id)
        return f"SUCCESS: Stopped background task '{target_job.name}' (ID: {target_job.id})."
    except Exception as e:
        return f"Error stopping task: {e}"

async def tool_list_tasks():
    pretty_log("Tool Call: List Tasks", None, icon="📋")
    jobs = scheduler.get_jobs()
    if not jobs:
        return "No active scheduled tasks."
    
    lines = ["ACTIVE SCHEDULED TASKS:"]
    for job in jobs:
        lines.append(f"- ID: {job.id} | Name: {job.name} | Next Run: {job.next_run_time}")
    return "\n".join(lines)


async def tool_read_file(filename: str):
    pretty_log("Tool Call: Read File", filename, icon=Icons.TOOL_FILE)
    if filename.lower().endswith(".pdf"):
        return "SYSTEM ERROR: `read_file` cannot read PDFs. Use `knowledge_base(action='ingest_document')` or `recall`."

    try:
        path = SANDBOX_DIR / os.path.basename(filename)
        if not path.exists(): return f"Error: File {filename} not found."

        content = await asyncio.to_thread(path.read_text)
        return content
    except Exception as e:
        return f"Error reading file: {e}"

async def tool_write_file(filename: str, content: str):
    pretty_log("Tool Call: Write File", f"Filename: {filename}", icon=Icons.MEM_SAVE)
    try:
        path = SANDBOX_DIR / os.path.basename(filename)
        await asyncio.to_thread(path.write_text, content)
        return f"Successfully wrote {len(content)} bytes to {filename}"
    except Exception as e:
        return f"Error writing file: {e}"

async def tool_list_files():
    """
    Lists all files currently in the Sandbox (Physical Disk) AND the Knowledge Base (Memory).
    """
    pretty_log("Tool Call: List Files", icon="📂")
    
    report = []
    
    # 1. Check Sandbox (Disk)
    try:
        files = os.listdir(SANDBOX_DIR)
        report.append(f"💾 PHYSICAL STORAGE ({len(files)} files): {files if files else '[Empty]'}")
    except Exception as e:
        report.append(f"💾 PHYSICAL STORAGE: Error ({e})")

    # 2. Check Memory (Vector DB)
    if memory_system:
        try:
            library = memory_system.get_library() or []
            report.append(f"🧠 KNOWLEDGE BASE ({len(library)} docs): {library if library else '[Empty]'}")
        except:
            report.append("🧠 KNOWLEDGE BASE: [Error reading index]")
    else:
        report.append("🧠 KNOWLEDGE BASE: [Disabled]")

    return "\n".join(report)

async def tool_download_file(url: str, filename: str = None):
    pretty_log("Tool Call: Download", f"{url} -> {filename}", icon="⬇️")

    # 1. Setup Tor Proxy
    proxy_url = os.getenv("TOR_PROXY", "socks5://127.0.0.1:9050")
    if proxy_url.startswith("socks5://"): 
        proxy_url = proxy_url.replace("socks5://", "socks5h://")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        # 2. Inject Proxy into Client
        async with httpx.AsyncClient(proxy=proxy_url, headers=headers, follow_redirects=True, timeout=30.0) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return f"Error: Download failed with status {resp.status_code}"

                if not filename:
                    cd = resp.headers.get("content-disposition", "")
                    if "filename=" in cd:
                        filename = cd.split("filename=")[-1].strip('"')
                    else:
                        filename = os.path.basename(urllib.parse.urlparse(url).path)
                        if not filename or "." not in filename:
                            filename = "downloaded_file.dat"

                filename = os.path.basename(filename)
                path = SANDBOX_DIR / filename

                sha256 = hashlib.sha256()
                with open(path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
                        sha256.update(chunk)

                file_hash = sha256.hexdigest()

        return f"Success: Downloaded '{filename}' ({path.stat().st_size} bytes). SHA256: {file_hash[:8]}. File is ready in sandbox."
    except Exception as e:
        return f"Error downloading file: {e}"

async def tool_remember(text: str):
    pretty_log("Tool Call: Remember", text, icon=Icons.CTX_LOAD)
    if not memory_system: return "Error: Memory system not active."

    try:
        meta = {"timestamp": get_utc_timestamp(), "type": "manual"}
        await asyncio.to_thread(memory_system.add, text, meta)
        return f"Memory stored: '{text}'"
    except Exception as e:
        return f"Error storing memory: {e}"


async def tool_gain_knowledge(filename: str):
    """
    Robustly reads a file (PDF/Text) OR a Website (via Tor), splits it into chunks,
    and saves it to Vector Memory. Tracks sources to prevent re-ingestion.
    """
    import time
    import fitz  # PyMuPDF
    from langchain.text_splitter import RecursiveCharacterTextSplitter

    # --- 1. INPUT SANITIZATION ---
    # Filenames on Linux/Unix cannot exceed 255 bytes.
    if len(filename) > 2000 or "\n" in filename:
        return "Error: Input contains newlines or is too long. Pass a valid Filename or URL."

    pretty_log("Tool Call: Gain Knowledge", filename, icon="🧠")

    if not memory_system:
        return "Error: Memory system is disabled."

    # --- 2. DUPLICATE DETECTION (Idempotency) ---
    current_library = memory_system.get_library()
    if filename in current_library:
        return f"Skipped: '{filename}' is already in the Knowledge Base. Use 'recall' to query it."

    # --- 3. SOURCE EXTRACTION ---
    full_text = ""
    is_web = filename.lower().startswith("http://") or filename.lower().startswith("https://")

    if is_web:
        # --- WEB PATH (TOR) ---
        pretty_log("Ingestion Phase 1", f"Fetching via Tor: {filename}", icon="🧅")
        try:
            # Reuse existing Tor-enabled helper
            full_text = await helper_fetch_url_content(filename)
            
            # Validation: helper returns error strings starting with "Error"
            if full_text.startswith("Error"):
                return full_text 
            
            if len(full_text) < 100:
                return "Error: Website content too short or blocked. Verification failed."
                
        except Exception as e:
            return f"Critical Web Error: {str(e)}"

    else:
        # --- DISK PATH (LOCAL) ---
        file_path = SANDBOX_DIR / filename
        if not file_path.exists():
            return f"Error: File '{filename}' not found in sandbox. Download it first."

        try:
            if filename.lower().endswith(".pdf"):
                pretty_log("Ingestion Phase 1", "Opening PDF stream...", icon="📂")
                try:
                    doc = fitz.open(file_path)
                except Exception as e:
                    return f"Error opening PDF: {str(e)}"

                total_pages = len(doc)
                pretty_log("Ingestion Details", f"Found {total_pages} pages. Starting extraction...", icon="⏳")

                start_time = time.time()
                page_timeout = 60 

                for i, page in enumerate(doc):
                    if time.time() - start_time > page_timeout:
                        pretty_log("Timeout Warning", f"Aborted at page {i}", level="WARN")
                        break
                    text = page.get_text()
                    if text: full_text += text + "\n"
                    if (i + 1) % 5 == 0:
                        pretty_log("Extraction Progress", f"Page {i + 1}/{total_pages}", icon="📄")
                doc.close()

            else:
                pretty_log("Ingestion Phase 1", "Reading Text File...", icon="📂")
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    full_text = f.read()

        except Exception as e:
            return f"Critical Disk Error: {str(e)}"

    if not full_text.strip():
        return "Error: Extracted text is empty."

    # --- PHASE 2: CHUNKING ---
    pretty_log("Ingestion Phase 2", f"Splitting {len(full_text)} chars...", icon="✂️")
    
    try:
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=100,
            separators=["\n\n", "\n", ".", " ", ""]
        )
        chunks = text_splitter.split_text(full_text)
    except Exception as e:
        return f"Error during text splitting: {e}"

    if not chunks:
        return "Error: No chunks created."

    # --- PHASE 3: EMBEDDING ---
    pretty_log("Ingestion Phase 3", f"Embedding {len(chunks)} chunks...", icon="💾")
    
    try:
        def batch_ingest(chunk_list, source_name):
            batch_size = 50 
            total = len(chunk_list)
            for i in range(0, total, batch_size):
                batch = chunk_list[i : i + batch_size]
                # Unique ID includes source name to avoid collisions
                ids = [hashlib.md5(f"{source_name}_{i+j}_{chunk[:20]}".encode()).hexdigest() for j, chunk in enumerate(batch)]
                metadatas = [{"source": source_name, "type": "document", "chunk_index": i+j, "timestamp": get_utc_timestamp()} for j in range(len(batch))]
                
                if hasattr(memory_system, "collection"):
                    memory_system.collection.upsert(
                        documents=batch,
                        metadatas=metadatas,
                        ids=ids
                    )
        
        await asyncio.to_thread(batch_ingest, chunks, filename)

        # Generate a mini-summary for the immediate response
        preview = full_text[:300].replace("\n", " ") + "..."

    except Exception as e:
        return f"Error during embedding: {e}"

    # --- PHASE 4: UPDATE INDEX ---
    try:
        await asyncio.to_thread(memory_system._update_library_index, filename, "add")
    except Exception as e:
        logger.error(f"Index update failed: {e}") 

    return (
        f"SUCCESS: Ingested '{filename}' ({len(chunks)} chunks) into Long-Term Memory.\n"
        f"--- CONTENT PREVIEW ---\n{preview}\n"
        f"(You can now use 'recall' to query this source or generate a full summary.)"
    )

async def tool_list_documents():
    """
    Lists all ingested documents. Safe for empty state.
    """
    pretty_log("Tool Call: List Documents", icon="📚")
    
    if not memory_system: 
        return "Error: Memory system disabled."

    library = memory_system.get_library() or []
    
    if not library:
        return "No documents found in memory."
        
    # Format the list nicely
    doc_list = "\n".join([f"- {doc}" for doc in library])
    return f"LIBRARY CONTENTS ({len(library)} files):\n{doc_list}"

async def tool_unified_forget(target: str):
    """
    UNIVERSAL DELETE: Improved to handle both Documents (by filename) and Facts (by semantic content).
    """
    pretty_log("Tool Call: Universal Forget", target, icon="🧹")
    if not memory_system: return "Report: Memory disabled."
    
    report = []
    
    # 1. DISK CLEANUP (Physical Files)
    try:
        disk_match = next((f for f in os.listdir(SANDBOX_DIR) if target.lower() in f.lower()), None)
        if disk_match:
            (SANDBOX_DIR / disk_match).unlink()
            report.append(f"✅ Disk: Deleted '{disk_match}'")
    except: pass

    # 2. MEMORY CLEANUP (Vector Database)
    try:
        # A. Metadata Delete (Targeting specific filenames/sources)
        data = await asyncio.to_thread(memory_system.collection.get, include=["metadatas"])
        all_sources = set()
        if data and "metadatas" in data:
            for meta in data["metadatas"]:
                if meta and "source" in meta:
                    all_sources.add(meta["source"])
        
        db_match = next((s for s in all_sources if target.lower() in s.lower()), None)
        if db_match:
            await asyncio.to_thread(memory_system.delete_document_by_name, db_match)
            report.append(f"✅ Memory: Wiped document '{db_match}'.")

        # B. Semantic Sweep (Targeting specific facts/text)
        # This is the critical fix for "Forget [Fact]"
        results = memory_system.collection.query(
            query_texts=[target],
            n_results=5 
        )
        
        ids_to_delete = []
        if results['ids']:
            for i, dist in enumerate(results['distances'][0]):
                text_preview = results['documents'][0][i]
                
                # AGGRESSIVE MATCHING:
                # 1. High similarity (Distance < 0.6) - Your logs showed 0.533, so this catches it.
                # 2. Substring match (if the target text is literally inside the memory)
                if dist < 0.6 or target.lower() in text_preview.lower():
                    ids_to_delete.append(results['ids'][0][i])
                    report.append(f"✅ Sweep: Forgot fact '{text_preview[:50]}...' (Score: {dist:.3f})")

        if ids_to_delete:
            memory_system.collection.delete(ids=ids_to_delete)

    except Exception as e:
        report.append(f"⚠️ Memory Error: {e}")

    if not report:
        return f"Report: Could not find any memory or file matching '{target}' to delete. Try being more specific."
        
    return "\n".join(report)


async def tool_reset_all_memory():
    pretty_log("Tool Call: Reset Memory", "DELETING EVERYTHING", icon="☢️")

    if not memory_system:
        return "Error: Memory system is disabled."

    try:
        all_ids = memory_system.collection.get()['ids']
        if all_ids:
            batch_size = 500
            for i in range(0, len(all_ids), batch_size):
                memory_system.collection.delete(ids=all_ids[i:i+batch_size])

        memory_system.library_file.write_text("[]")

        return "Success: The entire knowledge base has been wiped clean."
    except Exception as e:
        return f"Error resetting memory: {e}"

# --- CONSOLIDATED TOOLS (EXPOSED) ---

async def tool_manage_tasks(action: str, task_name: str = None, cron_expression: str = None, prompt: str = None, task_identifier: str = None):
    """Consolidated task management."""
    if action == "create":
        if not (task_name and cron_expression and prompt):
                return "Error: 'create' requires task_name, cron_expression, and prompt."
        return await tool_schedule_task(task_name, prompt, cron_expression)
    elif action == "list":
        return await tool_list_tasks()
    elif action == "stop":
        if not task_identifier: return "Error: 'stop' requires task_identifier."
        return await tool_stop_task(task_identifier)
    elif action == "stop_all":
        return await tool_stop_all_tasks()
    else:
        return f"Error: Unknown action '{action}'"

async def tool_knowledge_base(action: str, content: str = None, source: str = None):
    """Consolidated memory management."""
    # Support 'source' as an alias for 'content' (legacy compatibility)
    target = content or source
    
    if action == "insert_fact":
        if not target: return "Error: 'insert_fact' requires content (text)."
        return await tool_remember(target)
        
    elif action == "ingest_document":
        if not target: return "Error: 'ingest_document' requires content (the source/filename)."
        return await tool_gain_knowledge(target)
        
    # --- NEW UNIFIED FORGET ---
    elif action == "forget":
        if not target: return "Error: 'forget' requires content (what to forget)."
        return await tool_unified_forget(target)
    # --------------------------

    elif action == "list_docs":
            return await tool_list_documents()
    elif action == "reset_all":
            return await tool_reset_all_memory()
    else:
        return f"Error: Unknown action '{action}'"

async def tool_file_system(operation: str, path: str = None, content: str = None, **kwargs):
    """Consolidated file system operations with Auto-Correction."""
    
    # --- AUTO-CORRECTION: Handle 'filename' vs 'path' confusion ---
    if not path and "filename" in kwargs:
        path = kwargs["filename"]
        pretty_log("System Auto-Fix", f"Mapped 'filename' -> 'path': {path}", level="DEBUG")

    if operation == "list":
        return await tool_list_files()
        
    if not path: 
        return "Error: 'path' argument is required (e.g., 'data.txt')."

    if operation == "read":
        return await tool_read_file(path)
    elif operation == "write":
        if content is None: return "Error: 'write' requires 'content'."
        return await tool_write_file(path, content)
    elif operation == "download":
        return await tool_download_file(url=path, filename=content)
    else:
        return f"Error: Unknown operation '{operation}'"
        
# --- REMAINING INDEPENDENT TOOLS ---

async def tool_update_profile(category: str, key: str, value: str):
    pretty_log("Tool Call: Update Profile", f"{category} -> {key}={value}", icon="👤")
    
    if not profile_memory: 
        return "Error: Profile memory not loaded."

    msg = profile_memory.update(category, key, value)
    
    vector_fact = f"User's {key} is {value}"
    if memory_system:
        try:
            await asyncio.to_thread(memory_system.smart_update, vector_fact, "identity")
            pretty_log("Identity Sync", f"Fact '{vector_fact}' synced to Vector DB", icon="💾")
        except Exception as e:
            logger.error(f"Vector sync failed: {e}")

    return f"SUCCESS: Profile updated. {category}.{key} is now '{value}'."

import ast  # <--- Make sure this is at the top of your file

async def tool_execute(filename: str, content: str, args: List[str] = None):
    # --- 🛡️ HIJACK LAYER: CODE SANITIZATION ---
    # Granite-4 often produces JSON artifacts. We must unroll them before execution.
    
    # 1. Fix "Slash-N" Hallucination (Literal \n in code)
    # Fixes: SyntaxError: unexpected character after line continuation character
    if "\\n" in content:
        content = content.replace("\\n", "\n")

    # 2. Fix Escaped Quotes (The Docstring Crash)
    # Fixes: \"\"\"Return the first n...
    content = content.replace('\\"', '"')
    content = content.replace("\\'", "'")

    # 3. Fix Raw Regex Strings (The r\pattern Crash)
    # Fixes: re.search(r\d+, text) -> re.search(r'''\d+''', text)
    try:
        # Look for r\ followed by chars that are NOT quotes, spaces, or parens
        content = re.sub(r'(?<![\'"])r\\([^\s\),]+)', r"r'''\\\1'''", content)
    except Exception:
        pass 

    # 4. Remove Markdown Wrappers (Common "Chatty" artifact)
    # Removes ```python ... ``` if the model wraps the code
    content = re.sub(r'^```[a-zA-Z]*\n?', '', content, flags=re.MULTILINE)
    content = re.sub(r'```$', '', content, flags=re.MULTILINE)

    # 5. Final Trim
    content = content.strip()
    # ----------------------------------------

    pretty_log("Unified Execution", f"Target: {filename}", icon="⚡️")

    if not sandbox_manager: return "Error: Sandbox not active."
    
    host_path = SANDBOX_DIR / os.path.basename(filename)
    
    # --- STUBBORNNESS GUARD ---
    # Prevents the agent from retrying the exact same broken code 3 times
    if host_path.exists():
        try:
            existing_code = (await asyncio.to_thread(host_path.read_text))
            # Compare code ignoring whitespace to catch "fake" edits
            if "".join(existing_code.split()) == "".join(content.split()):
                pretty_log("Stubbornness Guard", "Blocked identical code", level="WARNING", icon="🛑")
                return (
                    "--- EXECUTION RESULT ---\n"
                    "EXIT CODE: 1\n"
                    "STDOUT/STDERR:\n"
                    "SYSTEM ERROR: You submitted the EXACT SAME CODE that failed previously. You MUST change the logic.\n"
                )
        except: pass

    # 1. WRITE THE FILE
    await asyncio.to_thread(host_path.write_text, content)
    
    # 2. AUTO-FORMATTING (Optional but recommended for Python)
    ext = filename.split('.')[-1].lower()
    if ext == "py":
        # 'Black' will standardize the quotes and indentation, often fixing minor syntax errors
        await asyncio.to_thread(sandbox_manager.execute, f"python3 -m black {filename}", timeout=15)

    # 3. EXECUTION COMMAND
    runtime_map = {"py": "python3 -u", "js": "node", "sh": "bash"}
    runner = runtime_map.get(ext, "chmod +x" if ext == "sh" else "")
    cmd = f"{runner} {filename}" if runner else f"./{filename}"
    
    # Add command line arguments if provided
    if args: 
        cmd += " " + " ".join([str(a).replace("'", "'\\''") for a in args])

    # Wrap in shell script to handle pipes/redirects cleanly
    wrapper = SANDBOX_DIR / f"_run_{uuid.uuid4().hex[:6]}.sh"
    wrapper.write_text(f"#!/bin/sh\n{cmd}\n")
    os.chmod(wrapper, 0o777)

    try:
        start = datetime.datetime.now()
        # Execute inside Docker
        out, code = await asyncio.to_thread(sandbox_manager.execute, f"./{wrapper.name}", timeout=120)
        duration = (datetime.datetime.now() - start).total_seconds()
        wrapper.unlink(missing_ok=True)

        # 4. DIAGNOSTICS & CONTEXT INJECTION
        diagnostic_info = ""
        
        if code != 0:
            if ext == "py":
                # Run pylint inside the container for clearer error messages
                pylint, _ = await asyncio.to_thread(sandbox_manager.execute, f"python3 -m pylint -E {filename}")
                if pylint: diagnostic_info += f"\n--- LINTING REPORT ---\n{pylint.strip()}\n"

            # Context Injector: Read the file output to find the error line
            tb_match = re.findall(r'File "([^"]+)", line (\d+),', out)
            if tb_match:
                last_error_file, last_error_line = tb_match[-1]
                # Only inject context if the error is in the file we just wrote
                if os.path.basename(last_error_file) == os.path.basename(filename):
                    try:
                        line_num = int(last_error_line)
                        lines = content.splitlines()
                        start_l = max(0, line_num - 3)
                        end_l = min(len(lines), line_num + 2)
                        snippet = "\n".join([f"{i+1}: {l}" for i, l in enumerate(lines) if start_l <= i < end_l])
                        
                        diagnostic_info += (
                            f"\n--- BUG LOCATION (Line {line_num}) ---\n"
                            f"{snippet}\n"
                            f"----------------------------------\n"
                            f"SYSTEM TIP: Look at Line {line_num} above."
                        )
                    except: pass

        if not out.strip(): out = "[NO OUTPUT PRODUCED]"
        
        return (
            f"--- EXECUTION RESULT ---\n"
            f"EXIT CODE: {code}\n"
            f"DURATION: {duration:.2f}s\n"
            f"STDOUT/STDERR:\n{out}\n"
            f"{diagnostic_info}"
        )

    except Exception as e:
        return f"Sandbox Failure: {e}"

async def tool_search_ddgs(query: str):
    pretty_log(f"Tool Call: Search [Anonymous/DDGS] Query='{query}'", f"Proxy: {TOR_PROXY}\nQuery: {query}", icon="🧅")
    
    def format_search_results(results: List[Dict]) -> str:
        if not results: return "No results found."
        formatted = []
        for i, res in enumerate(results, 1):
            title = res.get('title', 'No Title')
            body = res.get('body', res.get('content', 'No content'))
            link = res.get('href', res.get('url', '#'))
            formatted.append(f"### {i}. {title}\n{body}\n[Source: {link}]")
        return "\n\n".join(formatted)
    
    if not importlib.util.find_spec("ddgs"):
        return "Search unavailable (Library 'ddgs' not installed)."

    from ddgs import DDGS
    for attempt in range(3):
        try:
            def run():
                with DDGS(proxy=TOR_PROXY, timeout=15) as ddgs:
                    return list(ddgs.text(query, max_results=3))
            raw_results = await asyncio.to_thread(run)
            clean_output = format_search_results(raw_results)
            pretty_log("Tool Result: Search (DDGS)", clean_output, icon=Icons.TOOL_OK)
            return clean_output
        except Exception as e:
            logger.warning(f"DDGS attempt {attempt+1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(1)

    return "Error: Search failed after 3 retries."

async def tool_search_tavily(query: str):
    pretty_log(f"Tool Call: Search [Public/Tavily] Query='{query}'", f"Query: {query}", icon=Icons.TOOL_NET)

    def format_search_results(results: List[Dict]) -> str:
        if not results: return "No results found."
        formatted = []
        for i, res in enumerate(results, 1):
            title = res.get('title', 'No Title')
            body = res.get('body', res.get('content', 'No content'))
            link = res.get('href', res.get('url', '#'))
            formatted.append(f"### {i}. {title}\n{body}\n[Source: {link}]")
        return "\n\n".join(formatted)

    try:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

        def run():
            return tavily.search(query=query, search_depth="basic", max_results=5)

        raw_results = await asyncio.to_thread(run)
        clean_output = format_search_results(raw_results.get('results', []))

        pretty_log("Tool Result: Search (Tavily)", clean_output, icon=Icons.TOOL_OK)
        return clean_output

    except Exception as e:
        return f"Error: Tavily Search failed: {e}"

async def tool_search(query: str):
    if args.anonymous:
        return await tool_search_ddgs(query)
    else:
        return await tool_search_tavily(query)

async def tool_recall(query: str):
    """
    Retrieves memories with GROUNDING but allows synthesis.
    """
    pretty_log("Tool Call: Recall", query, icon="🔍")
    
    if not memory_system: 
        return "Error: Memory system is disabled."

    try:
        results = await asyncio.to_thread(memory_system.search_advanced, query, limit=5)
    except:
        return "Error: Memory retrieval failed."

    valid_chunks = []
    print(f"\n--- DEBUG: RECALL SCORES FOR '{query}' ---")
    for res in results:
        score = res.get('score', 1.0)
        source = res.get('metadata', {}).get('source', 'Unknown')
        text = res.get('text', '')
        
        print(f"   [Source: {source}] Distance: {score:.3f}")

        # CHANGED: Threshold raised to 2.0 to catch the 1.333 score
        if score < 1.3:
            valid_chunks.append(f"SOURCE: {source}\nCONTENT: {text}")
    print("-------------------------------------------\n")

    if valid_chunks:
        context_str = "\n\n".join(valid_chunks)
        return (
            f"SYSTEM: Found {len(valid_chunks)} relevant memories.\n"
            f"<retrieved_memory>\n{context_str}\n</retrieved_memory>\n"
            f"INSTRUCTION: Use the retrieved memory above to answer the user's request. "
            f"Combine it with your internal knowledge if necessary, but prioritize the facts provided above."
        )
    else:
        # Keep this strict - if memory is missing, admit it.
        return (
            "SYSTEM OBSERVATION: Zero relevant documents found.\n"
            "MANDATORY INSTRUCTION: You do not have this specific information in your database. "
            "If the user is asking for a specific saved file/fact, state that you cannot find it."
        )
    
async def tool_get_user_location():
    pretty_log("Tool Call: Get Location", None, icon="📍")
    if profile_memory:
        data = profile_memory.load()
        if data.get("location"):
            return f"User's location is: {data['location']}"
    return "Location is unknown."

async def tool_deep_research(query: str):
    pretty_log("Tool Call: Deep Research", query, icon="🔬")
    
    urls = []
    try:
        if args.anonymous and importlib.util.find_spec("ddgs"):
            from ddgs import DDGS
            with DDGS(proxy=TOR_PROXY, timeout=15) as ddgs:
                results = list(ddgs.text(query, max_results=2))
                urls = [r.get('href') for r in results]
        elif os.getenv("TAVILY_API_KEY"):
            from tavily import TavilyClient
            tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
            results = await asyncio.to_thread(tavily.search, query=query, max_results=2)
            urls = [r.get('url') for r in results.get('results', [])]
    except Exception as e:
        return f"Search failed: {e}"

    if not urls: return "No search results found."

    report = []
    
    sem = asyncio.Semaphore(2) 
    
    async def process_url(url):
        async with sem:
            pretty_log("Deep Research", f"Reading: {url}", icon="📖")
            text = await helper_fetch_url_content(url)
            
            preview = text[:5000] 
            
            return f"### SOURCE: {url}\n{preview}\n[...content truncated...]\n"

    tasks = [process_url(u) for u in urls]
    page_contents = await asyncio.gather(*tasks)
    
    full_report = "\n\n".join(page_contents)
    
    return f"--- DEEP RESEARCH RESULT ---\n{full_report}\n\nSYSTEM INSTRUCTION: Analyze the text above to answer the user's question."

async def tool_system_health():
    pretty_log("Tool Call: System Health Check", None, icon=Icons.SYS_HEALTH)
    report = ["SYSTEM HEALTH REPORT", "=" * 30]

    try:
        resp = await http_client.get("/health")
        status = "✅ Online" if resp.status_code == 200 else f"⚠️ Code {resp.status_code}"
        report.append(f"LLM Server      : {status} ({UPSTREAM_URL})")
    except:
        report.append(f"LLM Server      : ❌ Connection Failed")

    if sandbox_manager:
        try:
            sandbox_manager.ensure_running()
            out, code = await asyncio.to_thread(sandbox_manager.execute, "timeout 1s sleep 0.1")
            if code == 0:
                report.append(f"Execution Engine: ✅ Active")
            else:
                report.append(f"Execution Engine: ⚠️ Ready (Timeout util failed)")
        except Exception as e:
            report.append(f"Execution Engine: ❌ Critical Error")
    else:
        report.append("Execution Engine: ❌ Sandbox Manager Not Loaded")

    if memory_system:
        try:
            def get_count():
                return memory_system.collection.count()
            count = await asyncio.to_thread(get_count)
            report.append(f"Memory System   : ✅ Active ({count} items)")
        except Exception as e:
            report.append(f"Memory System   : ❌ DB Error")
    else:
        report.append("Memory System   : ⚠️ Disabled")

    return "\n".join(report)


AVAILABLE_TOOLS = {
    # ... keep your other tools ...
    "manage_tasks": tool_manage_tasks,
    "knowledge_base": tool_knowledge_base,
    "file_system": tool_file_system,
    "system_utility": tool_system_utility,
    "fact_check": tool_fact_check,

    # ENSURE THESE ARE CORRECT:
    "recall": tool_recall,
    "forget": tool_unified_forget,
    "list_files": tool_list_files,    
    "list_documents": tool_list_files, 
    
    "web_search": tool_search,
    "execute": tool_execute,
    "get_user_location": tool_get_user_location,
    "system_health_check": tool_system_health,
    "update_profile": tool_update_profile,
    "deep_research": tool_deep_research,
    "get_weather": tool_get_weather,  
    
    # Legacy wrappers if you use them:
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "download_file": tool_download_file,
    "gain_knowledge": tool_gain_knowledge,
    "reset_all_memory": tool_reset_all_memory,
    "remember": tool_remember,
    "schedule_task": tool_schedule_task,
    "list_tasks": tool_list_tasks,
    "stop_task": tool_stop_task,
    "stop_all_tasks": tool_stop_all_tasks,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "manage_tasks",
            "description": "ADMINISTRATIVE TOOL. STRICTLY FORBIDDEN unless the user explicitly asks to 'schedule', 'set a cron job', or 'repeat' a task. Usage: Creates background jobs that run automatically in the future. DO NOT use for 'checking', 'running', or 'listing' current items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "list", "stop", "stop_all"]},
                    "task_name": {"type": "string", "description": "Required for 'create' (e.g. 'Daily News Fetcher')"},
                    "cron_expression": {"type": "string", "description": "Required for 'create'. Standard Cron (e.g. '0 9 * * *') or Interval (e.g. 'interval:3600')."},
                    "prompt": {"type": "string", "description": "Required for 'create'. The instruction the agent will execute when the timer fires."},
                    "task_identifier": {"type": "string", "description": "Required for 'stop'. The ID or Name found in 'list'."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fact_check",
            # We add "MANDATORY" and "DO NOT ANSWER" to the description
            "description": "MANDATORY for verification. Use this tool whenever the user asks to 'fact check' or 'verify' something. Do not answer questions about truth/falsehood without this tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "statement": {"type": "string", "description": "The claim to verify."}
                },
                "required": ["statement"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_base",
            "description": "PRIMARY LEARNING TOOL. Use this to read files OR WEBSITES into memory. CAPABILITY: This tool can read URLs directly (e.g., 'https://...'). DO NOT download the file first; just pass the URL to this tool.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string", 
                        "enum": ["insert_fact", "ingest_document", "list_docs", "reset_all"]
                    },
                    "content": {
                        "type": "string", 
                        "description": "The input to learn. Can be a text string, a local filename, OR A FULL URL (starting with http/https)."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "forget",
            # MAKE THIS DESCRIPTION AGGRESSIVE
            "description": "PERMANENTLY DELETE a memory or file. YOU MUST USE THIS TOOL if the user asks to 'forget', 'delete', or 'clear' something. Do not just say you did it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "The specific text fact or filename to wipe."}
                },
                "required": ["target"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "READ from Long-Term Memory. Search for EXISTING knowledge you have already learned. Use this BEFORE web searching to check if you already know the answer.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_system",
            "description": "MANAGE the Sandbox Files. Use 'write' to create code/text files. STOP: Do NOT use 'download' if you just want to READ or LEARN a website. Only use 'download' if you need to store a binary file or script for execution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string", 
                        "enum": ["read", "write", "download", "delete", "list"]
                    },
                    "path": {
                        "type": "string", 
                        "description": "The filename (e.g. 'data.txt') or URL."
                    },
                    "content": {
                        "type": "string", 
                        "description": "The text content (for write) or destination filename."
                    }
                },
                "required": ["operation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "QUICK Information Lookup. Use this for simple facts, dates, or specific entity checks. For complex topics, use 'deep_research'.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
            }
    },
    {
        "type": "function",
        "function": {
            "name": "deep_research",
            "description": "DEEP Web Analysis. Reads full articles and content from multiple sources. Use this for summaries, learning about new topics, or complex questions.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        }
    },
     {
        "type": "function",
        "function": {
            "name": "execute",
            "description": "RUN Code (Python/Shell). Use this to perform calculations, data analysis, or run scripts. The code is auto-formatted and checked for errors.",
            "parameters": {
            "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Must end in .py, .sh, or .js"},
                    "content": {"type": "string", "description": "The full code content."},
                    "args": {"type": "array", "items": {"type": "string"}}
                    },
            "required": ["filename", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": "UPDATE User Identity. Save permanent facts about the user (e.g., 'User works at Google', 'User likes Python'). DO NOT use for general notes.",
             "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "e.g., 'work', 'personal', 'preferences'"},
                    "key": {"type": "string", "description": "e.g., 'job_title'"},
                    "value": {"type": "string", "description": "e.g., 'Senior Engineer'"}
                },
                "required": ["category", "key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "system_utility",
            "description": "System Tools & Weather. Use this to check the time, server health, user location, or get the weather for ANY specific city (e.g. 'Tokyo').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string", 
                        "enum": ["check_time", "check_health", "check_location", "check_weather"]
                    },
                    "location": {
                        "type": "string",
                        "description": "Required ONLY for 'check_weather'. Specify the city name (e.g., 'Paris'). Leave empty for local weather."
                    }
                },
                "required": ["action"]
            }
        }
    }
]

# --- APP LIFECYCLE ---
memory_system = None
agent_semaphore = asyncio.Semaphore(1)
profile_memory = None

async def run_proactive_task(task_id: str, prompt: str):
    """The Heartbeat: Triggers Ghost to think AND EXECUTES TOOLS."""
    pretty_log("Proactive Heartbeat", f"Task: {task_id}", icon="💓")
    
    payload = {
        "model": "ghost-agent",
        "messages": [
            {"role": "system", "content": "You are in AUTONOMOUS MODE. Execute the task and use tools if needed."},
            {"role": "user", "content": prompt}
        ],
        "tools": TOOL_DEFINITIONS,
        "tool_choice": "auto"
    }

    try:
        resp = await http_client.post("/v1/chat/completions", json=payload)
        if resp.status_code == 200:
            llm_resp = resp.json()
            choice = llm_resp.get("choices", [])[0]
            msg = choice.get("message", {})
            tool_calls = msg.get("tool_calls", [])

            if tool_calls:
                for tool in tool_calls:
                    fname = tool["function"]["name"]
                    try:
                        tool_args = json.loads(tool["function"]["arguments"])
                    except:
                        tool_args = {}

                    if fname in AVAILABLE_TOOLS:
                        pretty_log("Proactive Exec", f"Running {fname}", icon="⚙️")
                        result = await AVAILABLE_TOOLS[fname](**tool_args)
                        pretty_log("Proactive Result", str(result)[:200], icon="✅")
                    else:
                        logger.error(f"Proactive task tried unknown tool: {fname}")
            else:
                pretty_log("Proactive Task", "Agent thought but took no action.", icon="💭")

    except Exception as e:
        logger.error(f"Scheduled Task {task_id} failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, memory_system, sandbox_manager, profile_memory, scheduler

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    http_client = httpx.AsyncClient(base_url=UPSTREAM_URL, timeout=600.0, limits=limits)

    pretty_log("System Boot", "Starting initialization sequence...", icon=Icons.BOOT_START)

    if importlib.util.find_spec("docker"):
        try:
            pretty_log("Sandbox Init", "Connecting to Docker Engine...", icon=Icons.BOOT_DOCKER)
            sandbox_manager = DockerSandbox(SANDBOX_DIR)
            await asyncio.to_thread(sandbox_manager.ensure_running)

            out, code = await asyncio.to_thread(sandbox_manager.execute, "echo 'READY'", timeout=5)
            if code == 0:
                pretty_log("Sandbox Ready", "Engine is 100% Operational", icon=Icons.TOOL_OK)
            else:
                pretty_log("Sandbox Warning", "Engine ready but smoke test failed", level="WARNING", icon=Icons.TOOL_FAIL)
        except Exception as e:
            pretty_log("Sandbox Failed", str(e), level="ERROR", icon=Icons.TOOL_FAIL)

    try:
        pretty_log("Identity Load", "Loading User Profile...", icon="👤")
        profile_memory = ProfileMemory(MEMORY_DIR)
    except Exception as e:
        pretty_log("Identity Failed", f"Could not load profile: {e}", level="ERROR")
        profile_memory = None

    if not args.no_memory:
        if importlib.util.find_spec("chromadb"):
            try:
                pretty_log("Memory Load", "Initializing Vector Database...", icon=Icons.BOOT_MEMORY)
                memory_system = await asyncio.to_thread(VectorMemory, MEMORY_DIR, UPSTREAM_URL)
                pretty_log("Memory Ready", "Vector Database Online", icon=Icons.BOOT_MEMORY)
            except Exception as e:
                pretty_log("Memory Failed", f"ChromaDB Error: {e}", level="ERROR", icon=Icons.TOOL_FAIL)
        else:
            pretty_log("Memory Skip", "ChromaDB not installed", level="WARNING", icon=Icons.SYS_INFO)

    try:
        scheduler.start()
        pretty_log("Scheduler Online", "Loaded persistent tasks from ghost.db", icon="📆")
    except Exception as e:
        pretty_log("Scheduler Error", str(e), level="ERROR")

    pretty_log("System Online", "Agent is listening for requests", icon=Icons.BOOT_READY)

    yield 

    pretty_log("System Shutdown", "Cleaning up resources...", icon="🛑")
    
    if scheduler.running:
        scheduler.shutdown()
        pretty_log("Scheduler Offline", "Background tasks stopped and saved", icon="💤")

    if http_client:
        await http_client.aclose()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY_NAME = "X-Ghost-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)):
    if args.api_key and (not api_key or api_key != args.api_key):
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key

@app.get("/")
async def root_check():
    return Response(content="Ollama is running", media_type="text/plain")

@app.head("/")
async def root_head():
    return Response(content="OK", media_type="text/plain")

@app.get("/api/version")
async def api_version():
    return {"version": "0.1.24"}

@app.post("/api/show")
async def api_show(request: Request):
    return {
        "modelfile": "# Modelfile generated by Ghost Agent\nFROM ghost-model",
        "parameters": "stop \"\n\"",
        "template": "{{ .System }}\nUSER: {{ .Prompt }}\nASSISTANT: ",
        "details": {
            "format": "gguf",
            "family": "llama",
            "families": ["llama"],
            "parameter_size": "7B",
            "quantization_level": "Q4_0"
        }
    }

@app.post("/api/pull")
async def api_pull(request: Request):
    return {"status": "success"}

@app.delete("/api/delete")
async def api_delete(request: Request):
    return {"status": "success"}

# --- ADD THIS NEW ENDPOINT ---
@app.get("/v1/models", dependencies=[Security(verify_api_key)])
async def list_openai_models():
    """Expose the model list in OpenAI format for Msty/Clients"""
    return {
        "object": "list",
        "data": [
            {
                "id": "ghost-agent",
                "object": "model",
                "created": int(datetime.datetime.now().timestamp()),
                "owned_by": "ghost-system",
                "permission": [],
                "root": "ghost-agent",
                "parent": None,
            },
            # Optional: Expose the raw model name too if you want it selectable
            {
                "id": "default", 
                "object": "model",
                "created": int(datetime.datetime.now().timestamp()),
                "owned_by": "upstream",
            }
        ]
    }
# -----------------------------

@app.get("/v1/models", dependencies=[Security(verify_api_key)])
async def list_openai_models():
    """Expose the model list in OpenAI format for Msty/Clients"""
    return {
        "object": "list",
        "data": [
            {
                "id": "ghost-agent",
                "object": "model",
                "created": int(datetime.datetime.now().timestamp()),
                "owned_by": "ghost-system",
                "permission": [],
                "root": "ghost-agent",
                "parent": None,
            },
            {
                "id": "default", 
                "object": "model",
                "created": int(datetime.datetime.now().timestamp()),
                "owned_by": "upstream",
            }
        ]
    }


@app.get("/api/tags")
async def list_models():
    return {
        "models": [
            {
                "name": "ghost-agent",
                "model": "ghost-agent",
                "modified_at": get_utc_timestamp(),
                "size": 1000000000,
                "digest": "sha256:ghostagent",
                "details": {
                    "format": "gguf",
                    "family": "llama",
                    "families": ["llama"],
                    "parameter_size": "7B",
                    "quantization_level": "Q4_0"
                }
            },
            {
                "name": "latest",
                "model": "latest",
                "modified_at": get_utc_timestamp(),
                "size": 1000000000,
                "digest": "sha256:latest",
                "details": {
                    "format": "gguf",
                    "family": "llama",
                    "families": ["llama"],
                    "parameter_size": "7B",
                    "quantization_level": "Q4_0"
                }
            }
        ]
    }

@app.post("/api/generate", dependencies=[Security(verify_api_key)])
async def api_generate(request: Request):
    try: body = await request.json()
    except: return JSONResponse({"error": "Invalid JSON"}, 400)

    prompt = body.get("prompt", "")
    model = body.get("model", "default")
    stream = body.get("stream", False)

    chat_payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": stream
    }

    try:
        resp = await http_client.post("/v1/chat/completions", json=chat_payload)
        resp.raise_for_status()
        llm_resp = resp.json()
        content = llm_resp["choices"][0]["message"]["content"]
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)

    if stream:
         async def generator():
             yield json.dumps({
                 "model": model,
                 "created_at": get_utc_timestamp(),
                 "response": content,
                 "done": True
             }).encode('utf-8') + b"\n"
         return StreamingResponse(generator(), media_type="application/x-ndjson")
    else:
        return {
            "model": model,
            "created_at": get_utc_timestamp(),
            "response": content,
            "done": True
        }

async def openai_streamer(model: str, content: str, created_time: int, req_id: str):
    chunk_id = f"chatcmpl-{req_id}"
    start_chunk = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created_time,
        "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
    }
    yield f"data: {json.dumps(start_chunk)}\n\n".encode('utf-8')

    content_chunk = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created_time,
        "model": model, "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
    }
    yield f"data: {json.dumps(content_chunk)}\n\n".encode('utf-8')

    stop_chunk = {
        "id": chunk_id, "object": "chat.completion.chunk", "created": created_time,
        "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    }
    yield f"data: {json.dumps(stop_chunk)}\n\n".encode('utf-8')
    yield b"data: [DONE]\n\n"


@app.post("/chat", dependencies=[Security(verify_api_key)])
@app.post("/v1/chat/completions", dependencies=[Security(verify_api_key)])
@app.post("/api/chat", dependencies=[Security(verify_api_key)])
async def chat_proxy(request: Request, background_tasks: BackgroundTasks):
    req_id = str(uuid.uuid4())[:8]
    token = request_id_context.set(req_id)

    if http_client is None:
        return JSONResponse({"error": "System is still booting. Please wait 5 seconds."}, 503)

    try:
        pretty_log(f"Request Queueing", icon=Icons.REQ_QUEUE)

        async with agent_semaphore:
            pretty_log("Request Started (Lock Acquired)", special_marker="BEGIN")
            #gc.collect()

            try: body = await request.json()
            except: return JSONResponse({"error": "Invalid JSON"}, 400)

            messages = body.get("messages", [])
            model = body.get("model", "ghost-agent")
            stream_response = body.get("stream", False)

            # --- 1. INTENT DETECTION (Refined for Hybrid Tasks) ---
            user_msgs = [m.get("content", "").lower() for m in messages if m.get("role") == "user"]
            last_user_content = user_msgs[-1] if user_msgs else ""
            
            # A. Base Keywords
            coding_keywords = ["python", "bash", "sh", "script", "code", "def ", "import "]
            coding_actions = ["write", "run", "execute", "debug", "fix", "create", "generate", "count"]

            has_coding_intent = False
            lc = last_user_content

            # B. Check for Coding Signals
            if any(k in lc for k in coding_keywords):
                # If we see "Python" + "Run/Write", it's likely coding
                if any(a in lc for a in coding_actions):
                    has_coding_intent = True
            
            # C. Handle Research Overlaps
            # If the user asks to "learn" or "read", we usually default to General Mode...
            if "learn" in lc or "read" in lc or "ingest" in lc or "gain knowledge" in lc:
                 has_coding_intent = False

            # D. THE FIX: "Execution" Trumps "Learning"
            # If the user explicitly asks to "execute", "run script", or "write program",
            # we force Specialist Mode even if "gain knowledge" was mentioned.
            if "execute" in lc or "script" in lc or "word_count.py" in lc:
                has_coding_intent = True

            # --- 2. SYSTEM PROMPT SWAPPING (The Fix) ---
            # Instead of appending, we SWAP the prompt. 
            # This prevents "Assistant" instructions from conflicting with "Coder" instructions.
            
            profile_context = ""
            if profile_memory:
                profile_context = profile_memory.get_context_string()

            if has_coding_intent:
                # MODE 1: PYTHON SPECIALIST (Aggressive Tool Use)
                # We use the strict CODE_SYSTEM_PROMPT as the BASE.
                base_prompt = CODE_SYSTEM_PROMPT
                current_temperature = 0.2
                pretty_log("Context Manager", "ACTIVATED: Python Specialist Mode", level="INFO", icon="🐍")
                
                # We append a minimal profile just for context, but keep it brief
                if profile_context:
                    base_prompt += f"\n\nUSER CONTEXT (For variable naming only):\n{profile_context}"
            else:
                # MODE 2: GENERAL ASSISTANT
                base_prompt = SYSTEM_PROMPT
                current_temperature = args.temperature
                base_prompt = base_prompt.replace("{{PROFILE}}", profile_context)

            # Inject Time (Crucial for both modes)
            base_prompt = base_prompt.replace("{{CURRENT_TIME}}", datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

            # Update/Insert System Prompt
            if not any(m.get("role") == "system" for m in messages):
                messages.insert(0, {"role": "system", "content": base_prompt})
            else:
                for m in messages:
                    if m.get("role") == "system":
                        m["content"] = base_prompt
                        break

            # --- 3. CONTEXT & MEMORY INJECTION ---
            # "God Mode" Injection (Task List)
            lc_content = last_user_content.lower()
            if "task" in lc_content and ("list" in lc_content or "show" in lc_content or "what" in lc_content or "status" in lc_content):
                current_tasks = await tool_list_tasks()
                messages.append({
                    "role": "system", 
                    "content": (
                        f"SYSTEM DATA DUMP:\n{current_tasks}\n\n"
                        "INSTRUCTION: The user cannot see the data above. "
                        "You MUST copy the task list into your **FINAL ANSWER** now."
                    )
                })

            # Vector Memory (Skip for purely coding tasks to save context, unless requested)
            # This helps the model focus on the CODE, not on random past memories.
            is_fact_check = "fact-check" in last_user_content or "verify" in last_user_content
            
            # OPTIMIZATION: Define trivial triggers that don't need history
            trivial_triggers = ["time", "date", "weather", "who are you", "system health", "status"]
            is_trivial = any(t in last_user_content.lower() for t in trivial_triggers)

            # We ONLY inject memory if it's a general chat, NOT trivial, and NOT a fact check
            should_fetch_memory = (
                not is_fact_check and 
                not is_trivial and
                (not has_coding_intent or "remember" in last_user_content or "previous" in last_user_content or "recall" in last_user_content)
            )

            if memory_system and last_user_content and should_fetch_memory:
                context = memory_system.search(last_user_content)
                if context:
                    pretty_log("Context Injected", context[:100] + "...", icon=Icons.CTX_LOAD)
                    messages.insert(1, {"role": "system", "content": f"[MEMORY CONTEXT]:\n{context}"})
            
            messages = process_rolling_window(messages, args.max_context)

            final_ai_content = ""
            created_time = int(datetime.datetime.now().timestamp())

            force_stop = False
            seen_tools = set()
            tool_usage = {} 
            tools_run_this_turn = []
            redundancy_strikes = 0
            execution_failure_count = 0 

            last_was_failure = False

            # === MAIN INTERACTION LOOP ===
            for turn in range(20):
                if force_stop:
                    pretty_log("Task Complete", "Stopping interaction loop.", icon="🏁")
                    break
                
                # --- A. DYNAMIC TEMPERATURE (ERROR RECOVERY) ---
                if last_was_failure:
                    # If code fails, we boost temp to let it "think outside the box"
                    boost = 0.3 if has_coding_intent else 0.2
                    active_temp = current_temperature + boost
                    pretty_log("Brainstorming", f"Increasing variance to {active_temp:.2f} to solve error", icon="💡")
                else:
                    active_temp = current_temperature

                active_temp = min(active_temp, 0.95)

                # --- B. TOOL PRIORITIZATION ---
                # No filtering, but we rely on the PROMPT SWAP to force tool usage.
                active_tools = TOOL_DEFINITIONS

                payload = {
                    "model": model, 
                    "messages": messages, 
                    "stream": False,
                    "tools": active_tools,
                    "tool_choice": "auto",
                    "temperature": active_temp, 
                    "frequency_penalty": 0.5
                }

                pretty_log("LLM Interaction [REQUEST]", f"Temp: {active_temp:.2f}", icon=Icons.LLM_ASK)
                resp = await http_client.post("/v1/chat/completions", json=payload)
                resp.raise_for_status()
                llm_resp = resp.json()

                choice = llm_resp.get("choices", [])[0]
                msg = choice.get("message", {})
                content = msg.get("content", "") or ""
                tool_calls = msg.get("tool_calls", [])

                if "THOUGHT:" in content:
                    try: pretty_log("🤖 Model Thought", content.split("ACTION:", 1)[0].replace("THOUGHT:", "").strip(), icon="💭")
                    except: pass

                pretty_log("LLM Interaction [REPLY]", msg if DEBUG_MODE else None, icon=Icons.LLM_REPLY)

                if msg.get("content"): final_ai_content += msg.get("content")

                # --- C. FINAL ANSWER HANDLING ---
                if not tool_calls:
                    if args.smart_memory > 0.0 and memory_system and last_user_content:
                        was_remember_called = any(m.get("name") in ["knowledge_base", "update_profile"] for m in messages if m.get("role") == "tool")
                        if not was_remember_called:
                            interaction_log = f"User: {last_user_content}\nAI: {final_ai_content}"
                            background_tasks.add_task(run_smart_memory_task, interaction_log, model, args.smart_memory)

                    if stream_response:
                        return StreamingResponse(openai_streamer(model, final_ai_content, created_time, req_id), media_type="text/event-stream")

                    est_tokens = int(len(final_ai_content.split()) * 1.3)
                    duration_ns = int((datetime.datetime.now().timestamp() - created_time) * 1_000_000_000)
        
                    return JSONResponse({
                        "id": f"chatcmpl-{req_id}", "object": "chat.completion", "created": created_time, "model": model,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": final_ai_content}, "finish_reason": "stop"}],
                        "message": {"role": "assistant", "content": final_ai_content},
                        "done": True, "created_at": get_utc_timestamp(),
                        "eval_count": est_tokens, "total_duration": duration_ns
                    })

                # --- D. TOOL EXECUTION ---
                messages.append(msg)
                last_was_failure = False 
                tools_run_this_turn_hashes = set()

                for tool in tool_calls:
                    fname = tool["function"]["name"]
                    
                    tool_usage[fname] = tool_usage.get(fname, 0) + 1
                    
                    limit = 3
                    if fname == "execute":
                        limit = 15 # Allow heavy debugging cycles
                    
                    if tool_usage[fname] > limit:
                        pretty_log("Loop Breaker", f"Tool overuse: {fname} (Limit {limit})", icon="⛔")
                        error_report = f"SYSTEM: Execution Halted. Tool '{fname}' used too many times."
                        messages.append({"role": "system", "content": error_report})
                        force_stop = True
                        continue

                    try:
                        tool_args = json.loads(tool["function"]["arguments"])
                        args_hash = f"{fname}:{json.dumps(tool_args, sort_keys=True)}"
                    except:
                        tool_args = {}
                        args_hash = f"{fname}:error"

                    # Redundancy Check
                    if args_hash in tools_run_this_turn_hashes:
                        continue
                    tools_run_this_turn_hashes.add(args_hash)

                    if args_hash in seen_tools and fname != "execute":
                        redundancy_strikes += 1
                        pretty_log("Redundancy Guard", f"suppressed duplicate: {fname}", icon="⏭️")
                        messages.append({
                            "role": "tool", "tool_call_id": tool["id"], "name": fname,
                            "content": "SYSTEM MONITOR: You already executed this command. Review previous results."
                        })
                        if redundancy_strikes >= 3:
                            force_stop = True
                        last_was_failure = True
                        continue 
                    else:
                        if fname != "execute": redundancy_strikes = 0
                    
                    seen_tools.add(args_hash)
                    
                    if fname in AVAILABLE_TOOLS:
                        result = await AVAILABLE_TOOLS[fname](**tool_args)
                        str_result = str(result)
                        
                        # Truncate large outputs to prevent context overflow
                        if len(str_result) > 4000:
                             safe_content = str_result[:2000] + "\n...[TRUNCATED]...\n" + str_result[-2000:]
                        else:
                            safe_content = str_result

                        tool_msg = {"role": "tool", "tool_call_id": tool["id"], "name": fname, "content": safe_content}
                        messages.append(tool_msg)
                        tools_run_this_turn.append(tool_msg)

                        # --- E. ERROR HANDLING (Crucial for Tool Efficacy) ---
                        import re
                        exit_code_val = 0
                        is_error = False

                        if fname == "execute":
                            code_match = re.search(r"EXIT CODE:\s*(\d+)", str_result)
                            if code_match:
                                exit_code_val = int(code_match.group(1))
                            else:
                                exit_code_val = 1 
                            
                            is_error = (exit_code_val != 0)
                            
                            if is_error:
                                execution_failure_count += 1
                                pretty_log("Execution Failed", f"Strike {execution_failure_count}/3", level="INFO", icon="🔄")
                                if execution_failure_count >= 3:
                                    messages.append({
                                        "role": "system",
                                        "content": "SYSTEM: You have failed 3 times. STOP and explain why."
                                    })
                                    force_stop = True
                            else:
                                execution_failure_count = 0 

                        else:
                            is_error = "Error" in str_result or "failed" in str_result.lower()

                        if is_error:
                            last_was_failure = True
                            if fname == "execute" and not force_stop:
                                # Hints help the model fix code faster
                                error_hint = ""
                                if "SyntaxError" in str_result:
                                    error_hint += "\nSYSTEM TIP: Check quotes/parentheses. Do not use Markdown."

                                messages.append({
                                    "role": "system", 
                                    "content": (
                                        f"SYSTEM COMMAND: Execution Failed (Exit {exit_code_val}).\n"
                                        f"ERROR OUTPUT (Last 5 lines):\n{str_result[-500:]}\n"
                                        f"{error_hint}\n"
                                        "INSTRUCTION: Fix the code immediately."
                                    )
                                })

                        elif fname == "execute" and exit_code_val == 0:
                            pretty_log("System Logic", "✅ Success. Stopping loop.", icon="🛑")
                            messages.append({"role": "system", "content": "SYSTEM: Execution Success. STOP and report results."})
                            force_stop = True
                        
                        elif fname in ["manage_tasks"] and "SUCCESS" in str_result:
                             force_stop = True 

                    else:
                        messages.append({"role": "tool", "tool_call_id": tool["id"], "name": fname, "content": "Error: Unknown tool"})

                    #gc.collect()

            # --- FALLBACK ---
            if not final_ai_content:
                if tools_run_this_turn:
                    final_ai_content = f"Action Completed. Output:\n{tools_run_this_turn[-1]['content']}"
                else:
                    final_ai_content = "Process finished."

            if stream_response:
                return StreamingResponse(openai_streamer(model, final_ai_content, created_time, req_id), media_type="text/event-stream")
            
            return JSONResponse({
                "id": f"chatcmpl-{req_id}", "object": "chat.completion", "created": created_time, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": final_ai_content}, "finish_reason": "stop"}],
                "message": {"role": "assistant", "content": final_ai_content},
                "done": True, "created_at": get_utc_timestamp()
            })
    except Exception as e:
        pretty_log("Critical Error", str(e), level="ERROR", icon="🚨")
        return JSONResponse({"error": str(e)}, 500)
    finally:
        pretty_log("Request Finished", special_marker="END")
        request_id_context.reset(token)
        #gc.collect()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"], dependencies=[Security(verify_api_key)])
async def catch_all(request: Request, path: str):
    url = f"/{path}"

    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None) 

    try:
        req = http_client.build_request(
            request.method,
            url,
            headers=headers,
            content=request.stream()
        )
        r = await http_client.send(req, stream=True)
        return StreamingResponse(
            r.aiter_bytes(),
            status_code=r.status_code,
            media_type=r.headers.get("content-type")
        )
    except Exception as e:
        return JSONResponse({"error": f"Proxy Error: {e}"}, 502)

def daemonize():
    if os.name != 'posix': sys.exit(1)
    if os.fork() > 0: sys.exit(0)
    os.chdir("/")
    os.setsid()
    os.umask(0)
    if os.fork() > 0:
        with open(PID_FILE, 'w') as f: f.write(str(os.getpid()))
        sys.exit(0)
    sys.stdout.flush(); sys.stderr.flush()
    with open('/dev/null', 'r') as si: os.dup2(si.fileno(), sys.stdin.fileno())
    with open(LOG_FILE, 'a+') as so:
        os.dup2(so.fileno(), sys.stdout.fileno()); os.dup2(so.fileno(), sys.stderr.fileno())

if __name__ == "__main__":
    if args.daemon: daemonize()
    print(f"👻 Ghost Agent (Ollama Compatible) running on {args.host}:{args.port}")
    print(f"🔗 Connected to Upstream LLM at: {UPSTREAM_URL}")
    print(f"🔑 API Key: {args.api_key}")
    print(f"📏 Max Context: {args.max_context} tokens")

    if args.anonymous:
        print(f"🧅 Search Mode: ANONYMOUS (Tor + DuckDuckGo)")
        if not importlib.util.find_spec("ddgs"):
            print("⚠️  WARNING: 'ddgs' library not found. Search will fail.")
    else:
        print(f"🌐 Search Mode: PUBLIC (Tavily)")
        if not os.getenv("TAVILY_API_KEY"):
             print("⚠️  WARNING: TAVILY_API_KEY env var missing. Search will fail.")

    if args.smart_memory > 0.0:
        print(f"✨ Smart Memory: ENABLED (Selectivity Threshold: {args.smart_memory})")
    else:
        print("✨ Smart Memory: DISABLED")

    if not args.no_memory and importlib.util.find_spec("chromadb"):
        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils import embedding_functions

            logging.getLogger("chromadb").setLevel(logging.ERROR)

            _ef = embedding_functions.OpenAIEmbeddingFunction(
                api_key="sk-no-key-required",
                api_base=f"{UPSTREAM_URL}/v1",
                model_name="default"
            )

            _client = chromadb.PersistentClient(
                path=str(MEMORY_DIR),
                settings=Settings(allow_reset=True, anonymized_telemetry=False)
            )

            _coll = _client.get_or_create_collection(
                name="agent_memory",
                embedding_function=_ef
            )

            count = _coll.count()
            print(f"🧠 Long-Term Memories: {count}")

        except Exception as e:
            if DEBUG_MODE: print(f"⚠️  Could not read memory count: {e}")
    elif args.no_memory:
        print("🧠 Memory System: DISABLED (RAM Saving Mode)")

    print(f"🌡️  Temperature : {args.temperature} (Default)")

    if DEBUG_MODE: print("🐞 Debug Mode: ENABLED")
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)
