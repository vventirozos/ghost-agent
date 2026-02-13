import pytest
import asyncio
import json
import sys
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure agent module is loaded so we can patch imports
import ghost_agent.core.agent 
try:
    import httpx
except ImportError:
    httpx = None

from ghost_agent.core.agent import GhostAgent, GhostContext

def create_mock_llm_response(content=None, tool_calls=None):
    msg = {}
    if content: msg["content"] = content
    if tool_calls: msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}

@pytest.fixture
def mock_context():
    ctx = MagicMock(spec=GhostContext)
    ctx.args = MagicMock()
    ctx.args.max_context = 8000
    ctx.args.temperature = 0.5
    ctx.args.smart_memory = 0.0
    ctx.sandbox_dir = "/tmp/sandbox"
    ctx.memory_dir = "/tmp/memory"
    ctx.tor_proxy = None
    
    ctx.llm_client = AsyncMock()
    ctx.memory_system = MagicMock()
    ctx.scheduler = MagicMock()
    ctx.skill_memory = MagicMock()
    ctx.profile_memory = MagicMock()
    ctx.profile_memory.get_context_string.return_value = ""
    ctx.cached_sandbox_state = None
    
    ctx.scratchpad = MagicMock()
    ctx.scratchpad.list_all.return_value = "Nonex"
    ctx.memory_system.search.return_value = None
    ctx.scheduler.running = True
    ctx.scheduler.get_jobs.return_value = []
    
    # Mocking httpx ConnectError for the except block in agent.py
    # If agent.py imports httpx, we need to ensure it doesn't fail if we mock it out
    return ctx

# Helper for Plan JSON
def create_plan_response():
    return create_mock_llm_response(content=json.dumps({
        "thought": "Planning...",
        "tree_update": {},
        "next_action_id": "1"
    }))

@pytest.mark.asyncio
async def test_manage_tasks_continues_loop(mock_context):
    agent = GhostAgent(mock_context)
    # Tools must be async (awaitable)
    mock_tool = AsyncMock(return_value="SUCCESS: Task created.")
    agent.available_tools = {"manage_tasks": mock_tool}
    
    mock_httpx_obj = MagicMock()
    mock_httpx_obj.ConnectError = ConnectionError
    mock_httpx_obj.ConnectTimeout = TimeoutError
    
    with patch("ghost_agent.core.agent.httpx", new=mock_httpx_obj, create=True):
        mock_context.llm_client.chat_completion.side_effect = [
            create_plan_response(),
            create_mock_llm_response(tool_calls=[{
                "id": "call_1",
                "function": {"name": "manage_tasks", "arguments": "{}"}
            }]),
            create_plan_response(),
            create_mock_llm_response(content="I have created the task.")
        ]
        
        response, _, _ = await agent.handle_chat({"messages": [{"role": "user", "content": "Create a task"}]}, set())
        assert mock_context.llm_client.chat_completion.call_count == 4
        assert mock_tool.called

@pytest.mark.asyncio
async def test_learn_skill_continues_loop(mock_context):
    agent = GhostAgent(mock_context)
    mock_tool = AsyncMock(return_value="SUCCESS: Lesson saved.")
    agent.available_tools = {"learn_skill": mock_tool}
    
    mock_httpx_obj = MagicMock()
    mock_httpx_obj.ConnectError = ConnectionError
    mock_httpx_obj.ConnectTimeout = TimeoutError
    
    with patch("ghost_agent.core.agent.httpx", new=mock_httpx_obj, create=True):
        mock_context.llm_client.chat_completion.side_effect = [
            create_plan_response(),
            create_mock_llm_response(tool_calls=[{
                "id": "call_2",
                "function": {"name": "learn_skill", "arguments": "{}"}
            }]),
            create_plan_response(),
            create_mock_llm_response(content="Final response")
        ]
        response, _, _ = await agent.handle_chat({"messages": [{"role": "user", "content": "Save skill"}]}, set())
        assert mock_context.llm_client.chat_completion.call_count == 4
        assert mock_tool.called

@pytest.mark.asyncio
async def test_execute_success_continues_loop(mock_context):
    agent = GhostAgent(mock_context)
    mock_tool = AsyncMock(return_value="EXIT CODE: 0\nSTDOUT: Hello")
    agent.available_tools = {"execute": mock_tool}
    
    mock_fs = MagicMock()
    mock_fs.tool_list_files = MagicMock(return_value="Sandbox Mock") 
    
    mock_httpx_obj = MagicMock()
    mock_httpx_obj.ConnectError = ConnectionError
    mock_httpx_obj.ConnectTimeout = TimeoutError
    
    with patch("ghost_agent.core.agent.httpx", new=mock_httpx_obj, create=True), \
         patch.dict(sys.modules, {"ghost_agent.tools.file_system": mock_fs}):
        
        mock_context.llm_client.chat_completion.side_effect = [
            create_plan_response(),
            create_mock_llm_response(tool_calls=[{
                "id": "call_3",
                "function": {"name": "execute", "arguments": "{\"code\": \"print()\"}"}
            }]),
            create_plan_response(),
            create_mock_llm_response(content="Done.")
        ]
        response, _, _ = await agent.handle_chat({"messages": [{"role": "user", "content": "Run code"}]}, set())
        
        assert mock_context.llm_client.chat_completion.call_count == 4
        assert mock_tool.called
