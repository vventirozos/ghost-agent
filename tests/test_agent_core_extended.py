import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from ghost_agent.core.agent import GhostAgent, GhostContext

@pytest.fixture
def agent(mock_context):
    return GhostAgent(mock_context)

@pytest.mark.asyncio
async def test_agent_initialization(agent):
    assert agent.context is not None
    assert agent.available_tools is not None

@pytest.mark.asyncio
async def test_handle_chat_basic_flow(agent):
    # Mock LLM response
    agent.context.llm_client.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Hello User", "tool_calls": []}}]
    })
    
    body = {"messages": [{"role": "user", "content": "Hi"}], "model": "Qwen-Test"}
    content, _, _ = await agent.handle_chat(body, background_tasks=MagicMock())
    
    assert content == "Hello User"
    # Verify System Prompt Injection
    call_args = agent.context.llm_client.chat_completion.call_args[0][0]
    messages = call_args["messages"]
    assert messages[0]["role"] == "system"
    assert "Ghost" in messages[0]["content"]

@pytest.mark.asyncio
async def test_mode_switching_python_specialist(agent):
    # Mock LLM response
    agent.context.llm_client.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Code", "tool_calls": []}}]
    })
    
    # User asks for python code -> Should trigger specialist mode
    body = {"messages": [{"role": "user", "content": "Write a python script to count numbers"}], "model": "Qwen-Test"}
    await agent.handle_chat(body, background_tasks=MagicMock())
    
    call_args = agent.context.llm_client.chat_completion.call_args[0][0]
    messages = call_args["messages"]
    system_prompt = messages[0]["content"]
    
    assert "PYTHON SPECIALIST" in system_prompt
    assert "RAW, EXECUTABLE PYTHON CODE" in system_prompt

@pytest.mark.asyncio
async def test_history_truncation(agent):
    # Create long history
    msgs = [{"role": "user", "content": str(i)} for i in range(600)]
    body = {"messages": msgs, "model": "Qwen-Test"}
    
    agent.context.llm_client.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Done", "tool_calls": []}}]
    })
    
    await agent.handle_chat(body, background_tasks=MagicMock())
    
    call_args = agent.context.llm_client.chat_completion.call_args[0][0]
    sent_messages = call_args["messages"]
    
    # Should be truncated to approx 500 + system prompt + new msgs
    # The agent code: if len > 500: keep system + last 500
    # sent_messages includes the history sent to LLM
    assert len(sent_messages) <= 505 # Allow some buffer for injected system/memory prompts

@pytest.mark.asyncio
async def test_tool_execution_loop(agent):
    # Mock LLM to return a tool call then a final answer
    # Turn 1: Call tool
    agent.context.args.use_planning = False
    msg1 = {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "file_system", "arguments": '{"operation": "list"}'}
                }]
            }
        }]
    }
    msg2 = {
        "choices": [{"message": {"content": "Here are files", "tool_calls": []}}]
    }
    
    agent.context.llm_client.chat_completion = AsyncMock(side_effect=[msg1, msg2])
    
    # Mock Tool execution
    # IMPORTANT: The tool MUST return a string, otherwise pretty_log crashes when it tries to log it.
    agent.available_tools["file_system"] = AsyncMock(return_value="file1.txt")
    
    body = {"messages": [{"role": "user", "content": "List files"}], "model": "Qwen-Test"}
    await agent.handle_chat(body, background_tasks=MagicMock())
    
    # Verify tool was called
    agent.available_tools["file_system"].assert_called_once()

@pytest.mark.asyncio
async def test_planning_logic_trigger(agent):
    # Test triggering planning
    
    # 1. Simple task -> No planning
    # Explicitly disable planning for this part to avoid default Mock(True) behavior
    agent.context.args.use_planning = False
    
    agent.context.llm_client.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Simple Answer", "tool_calls": []}}]
    })
    
    with patch.object(agent, '_prepare_planning_context', return_value="Plan context") as mock_prep:
        body = {"messages": [{"role": "user", "content": "hi"}], "model": "Qwen-Test"}
        await agent.handle_chat(body, background_tasks=MagicMock())
        mock_prep.assert_not_called()

    # 2. Complex task -> Trigger planning
    # Enable planning
    agent.context.args.use_planning = True
    
    # We need to simulate turn > 0 or explicit complexity
    # OR we can mock `process_rolling_window` to return many messages?
    # Actually logic: if self.context.args.use_planning and turn == 0 and len(input) > 50 ...
    
    # Let's force it by mocking the check or conditions
    
    # Mock LLM response for the complex task
    agent.context.llm_client.chat_completion = AsyncMock(return_value={
        "choices": [{"message": {"content": "Complex Answer", "tool_calls": []}}]
    })

    with patch.object(agent, '_prepare_planning_context', return_value="Plan context") as mock_prep:
        body = {"messages": [{"role": "user", "content": "Write a complex python script to analyze stock data"}], "model": "Qwen-Test"}
        await agent.handle_chat(body, background_tasks=MagicMock())
        mock_prep.assert_called()
