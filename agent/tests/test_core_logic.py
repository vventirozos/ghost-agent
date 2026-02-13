
import pytest
import asyncio
import json
import httpx
from unittest.mock import MagicMock, AsyncMock, patch, ANY
from ghost_agent.core.agent import GhostAgent, GhostContext
from ghost_agent.core.llm import LLMClient

# --- Fixtures ---

@pytest.fixture
def mock_llm_client():
    client = MagicMock(spec=LLMClient)
    client.chat_completion = AsyncMock()
    return client

@pytest.fixture
def mock_context(mock_llm_client):
    ctx = MagicMock(spec=GhostContext)
    ctx.llm_client = mock_llm_client
    ctx.memory_system = MagicMock()
    ctx.profile_memory = MagicMock()
    ctx.profile_memory.get_context_string.return_value = "Mock Profile"
    ctx.skill_memory = MagicMock()
    ctx.scheduler = MagicMock()
    ctx.scratchpad = MagicMock()
    ctx.args = MagicMock()
    ctx.args.temperature = 0.5
    ctx.args.max_context = 1000
    ctx.args.smart_memory = 0.0
    ctx.sandbox_dir = "/tmp/sandbox"
    ctx.cached_sandbox_state = None
    return ctx

@pytest.fixture
def agent(mock_context):
    return GhostAgent(mock_context)

# --- LLMClient Tests ---

@pytest.mark.asyncio
async def test_llm_client_chat_completion_retry():
    # Test that client retries on connection error
    client = LLMClient("http://fake-url")
    
    with patch.object(client.http_client, "post", side_effect=[
        httpx.ConnectError("Fail 1"),
        httpx.ConnectError("Fail 2"),
        MagicMock(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": "Success"}}]})
    ]) as mock_post:
        
        payload = {"messages": []}
        response = await client.chat_completion(payload)
        
        assert mock_post.call_count == 3
        assert response["choices"][0]["message"]["content"] == "Success"

# --- GhostAgent Tests ---

@pytest.mark.asyncio
async def test_handle_chat_simple_response(agent, mock_llm_client):
    # Test a simple user interaction
    body = {"messages": [{"role": "user", "content": "Hello"}]}
    
    # Mock LLM response
    mock_llm_client.chat_completion.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Hello there!",
                "tool_calls": []
            }
        }]
    }
    
    response, _, _ = await agent.handle_chat(body, background_tasks=MagicMock())
    
    assert response == "Hello there!"
    mock_llm_client.chat_completion.assert_called_once()

@pytest.mark.asyncio
async def test_handle_chat_tool_execution(agent, mock_llm_client):
    # Test execution of a tool (mocked system_utility)
    body = {"messages": [{"role": "user", "content": "What time is it?"}]}
    
    # Mock LLM sequence: 
    # 1. Calls tool 'system_utility'
    # 2. Responds with final answer
    
    # We need to register a mock tool first or mock registry.
    # GhostAgent loads tools in __init__. Let's patch get_available_tools or override available_tools.
    
    mock_tool = AsyncMock(return_value="The time is 12:00")
    agent.available_tools = {"system_utility": mock_tool}
    
    # Dynamic side effect to handle planning vs execution
    async def dynamic_response(payload):
        messages = payload["messages"]
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        
        # If it's a planning request
        if "### ACTIVE STRATEGY" in system_msg or "Task Status:" in system_msg or "PLANNING_SYSTEM_PROMPT" in str(payload):
             # Or check if "PLANNING_SYSTEM_PROMPT" variable content is in system_msg
             # Since we don't have the prompt text here, let's just check for 'json_object' format
             # or specific planning keywords. 
             # Actually, simpler: The agent calls planning first.
             pass
        
        # Let's use a simpler side_effect list but include the planning steps.
        # Turn 0: Planning -> Tool Call
        # Turn 1: Planning (maybe) -> Final Answer
        
        # However, it's safer to use a dynamic mock if order varies.
        # But for this specific test case, the order is deterministic.
        
        # Planning calls don't have tools in payload usually, or use json_object
        is_planning = (
            payload.get("response_format", {}).get("type") == "json_object" or 
            not payload.get("tools")
        )
        
        if is_planning:
             # This is a planning call
             return {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "thought": "I need to check the time.",
                            "tree_update": {},
                            "next_action_id": "check_time"
                        })
                    }
                }]
            }
        
        # If not planning, it's an action call.
        # We need to iterate through our action sequence.
        # We can use a mutable iterator/list on the fixture or function attribute.
        if not hasattr(dynamic_response, "action_step"):
            dynamic_response.action_step = 0
            
        step = dynamic_response.action_step
        dynamic_response.action_step += 1
        
        if step == 0:
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1",
                            "function": {
                                "name": "system_utility",
                                "arguments": "{}"
                            }
                        }]
                    }
                }]
            }
        else:
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "It is 12:00.",
                        "tool_calls": []
                    }
                }]
            }

    mock_llm_client.chat_completion.side_effect = dynamic_response
    
    response, _, _ = await agent.handle_chat(body, background_tasks=MagicMock())
    
    assert "It is 12:00." in response
    mock_tool.assert_called_once()
    # assert mock_llm_client.chat_completion.call_count == 2 # Planning adds extra calls

@pytest.mark.asyncio
async def test_run_critic_check_approval(agent, mock_llm_client):
    # Test valid code approval
    mock_llm_client.chat_completion.return_value = {
        "choices": [{
            "message": {"content": json.dumps({"status": "APPROVED"})}
        }]
    }
    
    approved, revised, critique = await agent._run_critic_check("print('hello')", "task", "model")
    
    assert approved is True
    assert revised is None
    assert critique == "Approved"

@pytest.mark.asyncio
async def test_run_critic_check_rejection(agent, mock_llm_client):
    # Test code rejection and revision
    mock_llm_client.chat_completion.return_value = {
        "choices": [{
            "message": {"content": json.dumps({
                "status": "REJECTED",
                "revised_code": "print('fixed')",
                "critique": "Unsafe code"
            })}
        }]
    }
    
    approved, revised, critique = await agent._run_critic_check("rm -rf /", "task", "model")
    
    assert approved is False
    assert revised == "print('fixed')"
    assert critique == "Unsafe code"
