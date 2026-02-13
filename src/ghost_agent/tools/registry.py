from typing import Dict, Any, List, Callable
from .search import tool_search, tool_deep_research, tool_fact_check
from .file_system import tool_file_system
from .tasks import tool_manage_tasks
from .system import tool_system_utility
from .memory import tool_knowledge_base, tool_recall, tool_unified_forget, tool_update_profile, tool_learn_skill
from .execute import tool_execute

TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "system_utility", "description": "MANDATORY for Real-Time Data. Use this to check the current time, system health/status, user location, or get the weather. You DO NOT have access to these values without this tool.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["check_time", "check_weather", "check_health", "check_location"]}, "location": {"type": "string", "description": "Required ONLY for 'check_weather'. Specify the city name (e.g., 'Paris'). Leave empty for local weather."}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "file_system", "description": "Unified file manager. Use this to list, read, write, or download files.", "parameters": {"type": "object", "properties": {"operation": {"type": "string", "enum": ["list", "read", "write", "download", "search", "inspect"]}, "path": {"type": "string", "description": "The target filename (e.g., 'app.log'). MANDATORY for write/read/inspect."}, "content": {"type": "string", "description": "The text to write (MANDATORY for operation='write')."}, "url": {"type": "string", "description": "The URL to download (MANDATORY for operation='download')."}}, "required": ["operation", "path"]}}},
    {"type": "function", "function": {"name": "knowledge_base", "description": "Unified memory manager (ingest_document, forget, list_docs, reset_all).", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["ingest_document", "forget", "list_docs", "reset_all"]}, "content": {"type": "string"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "recall", "description": "Search long-term memory for facts, discussions, or document content.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "execute", "description": "Run Python or Shell code in a secure sandbox. ALWAYS print results.", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}}},
    {"type": "function", "function": {"name": "learn_skill", "description": "MANDATORY when you solve a complex bug or task after initial failure. Save the lesson so you don't repeat the mistake.", "parameters": {"type": "object", "properties": {"task": {"type": "string"}, "mistake": {"type": "string"}, "solution": {"type": "string"}}, "required": ["task", "mistake", "solution"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "Search the internet (Anonymous via Tor).", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "deep_research", "description": "Performs deep analysis by searching multiple sources and synthesizing a report.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fact_check", "description": "Verify a claim using deep research and external sources.", "parameters": {"type": "object", "properties": {"statement": {"type": "string"}}, "required": ["statement"]}}},
    {"type": "function", "function": {"name": "update_profile", "description": "Save a permanent fact about the user (name, preferences, location).", "parameters": {"type": "object", "properties": {"category": {"type": "string", "enum": ["root", "projects", "notes", "relationships"]}, "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["category", "key", "value"]}}},
    {"type": "function", "function": {"name": "manage_tasks", "description": "Consolidated task manager (create, list, stop, stop_all).", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["create", "list", "stop", "stop_all"]}, "task_name": {"type": "string"}, "cron_expression": {"type": "string"}, "prompt": {"type": "string"}, "task_identifier": {"type": "string"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "dream_mode", "description": "Triggers Active Memory Consolidation. Use this when the user asks to 'sleep', 'rest', or 'consolidate memories'.", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "replan", "description": "Call this tool if your current strategy is failing or if you need to pause and rethink. It forces a fresh planning step.", "parameters": {"type": "object", "properties": {"reason": {"type": "string", "description": "Why are you replanning?"}}, "required": ["reason"]}}}
]

def get_available_tools(context):
    from .memory import tool_dream_mode # Lazy import to avoid circular dependencies
    return {
        "system_utility": lambda **kwargs: tool_system_utility(tor_proxy=context.tor_proxy, profile_memory=context.profile_memory, context=context, **kwargs),
        "file_system": lambda **kwargs: tool_file_system(sandbox_dir=context.sandbox_dir, tor_proxy=context.tor_proxy, **kwargs),
        "knowledge_base": lambda **kwargs: tool_knowledge_base(sandbox_dir=context.sandbox_dir, memory_system=context.memory_system, profile_memory=context.profile_memory, **kwargs),
        "recall": lambda **kwargs: tool_recall(memory_system=context.memory_system, **kwargs),
        "execute": lambda **kwargs: tool_execute(sandbox_dir=context.sandbox_dir, sandbox_manager=context.sandbox_manager, memory_dir=context.memory_dir, **kwargs),
        "learn_skill": lambda **kwargs: tool_learn_skill(skill_memory=context.skill_memory, memory_system=context.memory_system, **kwargs),
        "web_search": lambda **kwargs: tool_search(anonymous=context.args.anonymous, tor_proxy=context.tor_proxy, **kwargs),
        "deep_research": lambda **kwargs: tool_deep_research(anonymous=context.args.anonymous, tor_proxy=context.tor_proxy, **kwargs),
        "fact_check": lambda **kwargs: tool_fact_check(http_client=context.llm_client.http_client, tool_definitions=TOOL_DEFINITIONS, deep_research_callable=lambda q: tool_deep_research(query=q, anonymous=context.args.anonymous, tor_proxy=context.tor_proxy), **kwargs),
        "update_profile": lambda **kwargs: tool_update_profile(profile_memory=context.profile_memory, memory_system=context.memory_system, **kwargs),
        "manage_tasks": lambda **kwargs: tool_manage_tasks(scheduler=context.scheduler, memory_system=context.memory_system, **kwargs),
        "dream_mode": lambda **kwargs: tool_dream_mode(context=context),
        "replan": lambda reason: f"Strategy Reset Triggered. Reason: {reason}\nSYSTEM: The planner will sees this and should update the TaskTree accordingly."
    }
