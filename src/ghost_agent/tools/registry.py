from typing import Dict, Any, List, Callable
from .search import tool_search, tool_deep_research, tool_fact_check
from .file_system import tool_file_system, tool_read_file, tool_write_file, tool_download_file, tool_list_files, tool_file_search, tool_inspect_file
from .tasks import tool_manage_tasks, tool_schedule_task, tool_list_tasks, tool_stop_task, tool_stop_all_tasks
from .system import tool_system_utility, tool_get_weather, tool_get_current_time, tool_system_health
from .memory import tool_knowledge_base, tool_recall, tool_unified_forget, tool_remember, tool_gain_knowledge, tool_scratchpad, tool_update_profile
from .execute import tool_execute

TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "system_utility", "description": "System Tools & Weather. Use this to check the time, server health, user location, or get the weather for ANY specific city (e.g. 'Tokyo').", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["check_time", "check_weather", "check_health", "check_location"]}, "location": {"type": "string", "description": "Required ONLY for 'check_weather'. Specify the city name (e.g., 'Paris'). Leave empty for local weather."}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "save_variable", "description": "Save a variable (key, value) to WORKING MEMORY.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "read_variable", "description": "Read a variable from WORKING MEMORY.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create a local file (name, content).", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a local file.", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}}},
    {"type": "function", "function": {"name": "list_files", "description": "Show the recursive tree of all files in the sandbox.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "download_file", "description": "Download URL to a local filename.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "filename": {"type": "string"}}, "required": ["url", "filename"]}}},
    {"type": "function", "function": {"name": "file_search", "description": "Grep for a pattern in a local file (or all files).", "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "filename": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "inspect_file", "description": "Peek at file headers.", "parameters": {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}}},
    {"type": "function", "function": {"name": "knowledge_base", "description": "PRIMARY LEARNING TOOL. Use this to read files OR WEBSITES into memory. This tool can read URLs directly (e.g., 'https://...').", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["ingest_document", "list_docs", "update_profile"]}, "content": {"type": "string"}}, "required": ["action"]}}},
    {"type": "function", "function": {"name": "recall", "description": "READ from Long-Term Memory. Search for EXISTING knowledge you have already learned. Use this BEFORE web searching.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "execute", "description": "RUN Code (Python/Shell). Use this to perform calculations, data analysis, or run scripts.", "parameters": {"type": "object", "properties": {"filename": {"type": "string", "description": "Must end in .py, .sh, or .js"}, "content": {"type": "string", "description": "The full code content."}}, "required": ["filename", "content"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "QUICK Information Lookup. Use this for simple facts, dates, or specific entity checks.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "deep_research", "description": "DEEP Web Analysis. Reads full articles and content from multiple sources.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fact_check", "description": "MANDATORY for verification. Use this tool whenever the user asks to 'fact check' or 'verify' something.", "parameters": {"type": "object", "properties": {"statement": {"type": "string"}}, "required": ["statement"]}}},
    {"type": "function", "function": {"name": "forget", "description": "PERMANENTLY DELETE a memory or file. YOU MUST USE THIS TOOL if the user asks to 'forget' or 'delete' something.", "parameters": {"type": "object", "properties": {"target": {"type": "string", "description": "Filename or fact to forget"}}, "required": ["target"]}}},
    {"type": "function", "function": {"name": "manage_tasks", "description": "ADMINISTRATIVE TOOL. Usage: Creates background jobs that run automatically in the future. Use ONLY for scheduling.", "parameters": {"type": "object", "properties": {"action": {"type": "string", "enum": ["create", "list", "stop", "stop_all"]}, "task_name": {"type": "string"}, "cron_expression": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["action"]}}}
]

def get_available_tools(context):
    return {
        "save_variable": lambda **kwargs: tool_scratchpad(action="set", scratchpad=context.scratchpad, **kwargs),
        "read_variable": lambda **kwargs: tool_scratchpad(action="get", scratchpad=context.scratchpad, **kwargs),
        "write_file": lambda **kwargs: tool_write_file(sandbox_dir=context.sandbox_dir, **kwargs),
        "read_file": lambda **kwargs: tool_read_file(sandbox_dir=context.sandbox_dir, **kwargs),
        "download_file": lambda **kwargs: tool_download_file(sandbox_dir=context.sandbox_dir, tor_proxy=context.tor_proxy, **kwargs),
        "file_search": lambda **kwargs: tool_file_search(sandbox_dir=context.sandbox_dir, **kwargs),
        "inspect_file": lambda **kwargs: tool_inspect_file(sandbox_dir=context.sandbox_dir, **kwargs),
        "knowledge_base": lambda **kwargs: tool_knowledge_base(sandbox_dir=context.sandbox_dir, memory_system=context.memory_system, scratchpad=context.scratchpad, profile_memory=context.profile_memory, **kwargs),
        "recall": lambda **kwargs: tool_recall(memory_system=context.memory_system, **kwargs),
        "execute": lambda **kwargs: tool_execute(sandbox_dir=context.sandbox_dir, sandbox_manager=context.sandbox_manager, **kwargs),
        "web_search": lambda **kwargs: tool_search(anonymous=context.args.anonymous, tor_proxy=context.tor_proxy, **kwargs),
        "deep_research": lambda **kwargs: tool_deep_research(anonymous=context.args.anonymous, tor_proxy=context.tor_proxy, **kwargs),
        "fact_check": lambda **kwargs: tool_fact_check(http_client=context.llm_client.http_client, tool_definitions=TOOL_DEFINITIONS, deep_research_callable=lambda **qa: tool_deep_research(anonymous=context.args.anonymous, tor_proxy=context.tor_proxy, **qa), **kwargs),
        "system_utility": lambda **kwargs: tool_system_utility(tor_proxy=context.tor_proxy, profile_memory=context.profile_memory, **kwargs),
        "system_health_check": lambda **kwargs: tool_system_health(upstream_url=context.args.upstream_url, http_client=context.llm_client.http_client, sandbox_manager=context.sandbox_manager, memory_system=context.memory_system),
        "forget": lambda **kwargs: tool_unified_forget(sandbox_dir=context.sandbox_dir, memory_system=context.memory_system, **kwargs),
        "manage_tasks": lambda **kwargs: tool_manage_tasks(scheduler=context.scheduler, memory_system=context.memory_system, **kwargs),
    }