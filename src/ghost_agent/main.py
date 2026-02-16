import argparse
import asyncio
import datetime
import importlib.util
import os
import sys
import json
import logging
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from .api.app import create_app
from .core.agent import GhostAgent, GhostContext
from .core.llm import LLMClient
from .memory.vector import VectorMemory
from .memory.profile import ProfileMemory
from .memory.scratchpad import Scratchpad
from .memory.skills import SkillMemory
from .sandbox.docker import DockerSandbox
from .utils.logging import setup_logging, pretty_log, Icons
from .utils.token_counter import load_tokenizer
from .tools import tasks
from .tools.registry import TOOL_DEFINITIONS

logger = logging.getLogger("GhostAgent")

def parse_args():
    parser = argparse.ArgumentParser(description="Ghost Agent: Autonomous AI Service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--upstream-url", default="http://127.0.0.1:8080")
    parser.add_argument("--temperature", "-t", type=float, default=0.7)
    parser.add_argument("--daemon", "-d", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true", help="Disable log truncation for debugging")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--max-context", type=int, default=32768)
    parser.add_argument("--api-key", default=os.getenv("GHOST_API_KEY", "ghost-secret-123"))
    parser.add_argument("--smart-memory", type=float, default=0.0)
    parser.add_argument("--anonymous", action="store_true", default=True, help="Always use anonymous search (Tor + DuckDuckGo)")
    return parser.parse_args()

@asynccontextmanager
async def lifespan(app):
    args = app.state.args
    context = app.state.context
    
    context.llm_client = LLMClient(args.upstream_url, context.tor_proxy)
    
    pretty_log("System Boot", "Initializing components", icon=Icons.SYSTEM_BOOT)

    if importlib.util.find_spec("docker"):
        try:
            context.sandbox_manager = DockerSandbox(context.sandbox_dir, context.tor_proxy)
            await asyncio.to_thread(context.sandbox_manager.ensure_running)
        except Exception as e:
            pretty_log("Sandbox Failed", str(e), level="ERROR", icon=Icons.FAIL)

    try:
        context.profile_memory = ProfileMemory(context.memory_dir)
    except Exception as e:
        pretty_log("Identity Failed", str(e), level="ERROR", icon=Icons.FAIL)

    if not args.no_memory:
        try:
            context.memory_system = VectorMemory(context.memory_dir, args.upstream_url, context.tor_proxy)
            if context.memory_system.collection:
                count = context.memory_system.collection.count()
                pretty_log("Memory Ready", f"{count} fragments indexed", icon=Icons.MEM_READ)
            else:
                pretty_log("Memory Offline", "Collection not loaded", level="WARNING", icon=Icons.WARN)
        except Exception as e:
            pretty_log("Memory Failed", str(e), level="ERROR", icon=Icons.FAIL)

    # Scheduler setup
    db_url = f"sqlite:///{(context.memory_dir / 'ghost.db').absolute()}"
    jobstores = {'default': SQLAlchemyJobStore(url=db_url)}
    context.scheduler = AsyncIOScheduler(jobstores=jobstores)
    
    agent = GhostAgent(context)
    app.state.agent = agent
    

    # --- IDLE MONITORING REMOVED ---
    # The automatic RAM cleanup after inactivity has been disabled per user request.
    # -------------------------------

    # ----------------------------
    
    # Real proactive task runner
    async def proactive_runner(task_id, prompt):
        pretty_log("Proactive Run", f"Task: {task_id}", icon=Icons.BRAIN_PLAN)
        payload = {
            "model": "Qwen3-4B-Instruct-2507",
            "messages": [
                {"role": "system", "content": "You are in AUTONOMOUS MODE. Execute the task and use tools if needed."},
                {"role": "user", "content": prompt}
            ],
            "tools": TOOL_DEFINITIONS,
            "tool_choice": "auto"
        }
        try:
            data = await context.llm_client.chat_completion(payload)
            msg = data.get("choices", [])[0].get("message", {})
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                for tool in tool_calls:
                    fname = tool["function"]["name"]
                    try: t_args = json.loads(tool["function"]["arguments"])
                    except: t_args = {}
                    if fname in agent.available_tools:
                        pretty_log("Proactive Tool", fname, icon=Icons.TOOL_CODE)
                        result = await agent.available_tools[fname](**t_args)
                        pretty_log("Proactive Ok", result, icon=Icons.OK)
            else:
                pretty_log("Proactive Idle", "No action required", icon=Icons.BRAIN_THINK)
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")

    tasks.run_proactive_task_fn = proactive_runner

    try:
        context.scheduler.start()
        pretty_log("Scheduler Ready", "Jobs loaded", icon=Icons.BRAIN_PLAN)
    except Exception as e:
        pretty_log("Scheduler Error", str(e), level="ERROR", icon=Icons.FAIL)

    pretty_log("System Ready", "Listening for requests", icon=Icons.SYSTEM_READY)

    yield
    
    if context.scheduler.running:
        context.scheduler.shutdown()
    await context.llm_client.close()

def main():
    args = parse_args()
    base_dir = Path(os.getenv("GHOST_HOME", Path.home() / "ghost_llamacpp"))
    sandbox_dir = base_dir / "sandbox"
    memory_dir = base_dir / "system" / "memory"
    log_file = base_dir / "system" / "ghost-agent.log"
    tokenizer_path = base_dir / "system" / "tokenizer"
    tor_proxy = os.getenv("TOR_PROXY", "socks5://127.0.0.1:9050")
    
    setup_logging(str(log_file), args.debug, args.daemon, args.verbose)
    load_tokenizer(tokenizer_path)
    
    # Ensure directories exist
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    memory_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"👻 Ghost Agent (Ollama Compatible) running on {args.host}:{args.port}")
    print(f"🔗 Connected to Upstream LLM at: {args.upstream_url}")
    print(f"📏 Max Context: {args.max_context} tokens")

    # Tavily support removed. Always using ANONYMOUS search.
    print(f"🧅 Search Mode: ANONYMOUS (Tor + DuckDuckGo)")
    if not importlib.util.find_spec("ddgs"):
        print("⚠️  WARNING: 'ddgs' library not found. Search will fail.")

    if args.smart_memory > 0.0:
        print(f"✨ Smart Memory: ENABLED (Selectivity Threshold: {args.smart_memory})")
    else:
        print("✨ Smart Memory: DISABLED")

    context = GhostContext(args, sandbox_dir, memory_dir, tor_proxy)
    context.scratchpad = Scratchpad()
    context.skill_memory = SkillMemory(memory_dir)
    
    app = create_app()
    app.router.lifespan_context = lifespan
    app.state.args = args
    app.state.context = context
    
    uvicorn.run(app, host=args.host, port=args.port, log_config=None)

if __name__ == "__main__":
    main()
