from typing import Dict, Any, List, Callable
from .search import tool_search, tool_deep_research, tool_fact_check, tool_read_web_page
from .file_system import tool_file_system
from .tasks import tool_manage_tasks
from .system import tool_system_utility
from .memory import tool_knowledge_base, tool_recall, tool_unified_forget, tool_update_profile, tool_learn_skill
from .execute import tool_execute
from .shell import tool_shell

from .schemas import (
    SystemUtility, FileSystem, KnowledgeBase, Recall, Execute, LearnSkill, 
    WebSearch, DeepResearch, FactCheck, UpdateProfile, ManageTasks, DreamMode, Replan,
    ReadWebPage, Shell
)
from pydantic import BaseModel

def pydantic_to_tool(name: str, model: type[BaseModel]) -> Dict[str, Any]:
    schema = model.model_json_schema()
    # Cleanup schema for cleaner LLM context
    if "title" in schema: del schema["title"]
    
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": model.__doc__ or "",
            "parameters": schema
        }
    }

TOOL_DEFINITIONS = [
    pydantic_to_tool("system_utility", SystemUtility),
    pydantic_to_tool("file_system", FileSystem),
    pydantic_to_tool("knowledge_base", KnowledgeBase),
    pydantic_to_tool("recall", Recall),
    pydantic_to_tool("execute", Execute),
    pydantic_to_tool("learn_skill", LearnSkill),
    pydantic_to_tool("web_search", WebSearch),
    pydantic_to_tool("deep_research", DeepResearch),
    pydantic_to_tool("fact_check", FactCheck),
    pydantic_to_tool("update_profile", UpdateProfile),
    pydantic_to_tool("manage_tasks", ManageTasks),
    pydantic_to_tool("dream_mode", DreamMode),
    pydantic_to_tool("replan", Replan),
    pydantic_to_tool("read_web_page", ReadWebPage),
    pydantic_to_tool("shell", Shell)
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
        "fact_check": lambda **kwargs: tool_fact_check(http_client=context.llm_client.http_client, tool_definitions=TOOL_DEFINITIONS, deep_research_callable=lambda q: tool_deep_research(query=q, anonymous=context.args.anonymous, tor_proxy=context.tor_proxy), web_search_callable=lambda query: tool_search(query=query, anonymous=context.args.anonymous, tor_proxy=context.tor_proxy), **kwargs),
        "update_profile": lambda **kwargs: tool_update_profile(profile_memory=context.profile_memory, memory_system=context.memory_system, **kwargs),
        "manage_tasks": lambda **kwargs: tool_manage_tasks(scheduler=context.scheduler, memory_system=context.memory_system, **kwargs),
        "dream_mode": lambda **kwargs: tool_dream_mode(context=context),
        "replan": lambda reason: tool_replan(reason),
        "read_web_page": lambda **kwargs: tool_read_web_page(**kwargs),
        "shell": lambda **kwargs: tool_shell(**kwargs)
    }

async def tool_replan(reason: str):
    return f"Strategy Reset Triggered. Reason: {reason}\nSYSTEM: The planner will sees this and should update the TaskTree accordingly."

