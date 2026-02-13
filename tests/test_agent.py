
import pytest
from unittest.mock import MagicMock
from ghost_agent.core.agent import GhostAgent, GhostContext

@pytest.fixture
def mock_agent():
    ctx = MagicMock(spec=GhostContext)
    # Mocking attributes accessed in specific methods if needed
    agent = GhostAgent(context=ctx)
    return agent

def test_prepare_planning_context_truncation(mock_agent):
    # Case 1: Short output
    tools_run = [{"content": "Short output"}]
    result = mock_agent._prepare_planning_context(tools_run)
    assert result == "Short output"

    # Case 2: Long output (Over 5000 chars)
    # create a string of 6000 chars
    long_content = "A" * 6000
    result = mock_agent._prepare_planning_context([{"content": long_content}])
    
    # Expect truncation
    assert len(result) < 6000
    assert "...[TRUNCATED]..." in result
    assert result.startswith("AAAA")
    assert result.endswith("AAAA")
    
    # Verify exact length: 2500 + len(marker) + 2500 ~= 5017ish
    assert len(result) == 2500 + len("\n...[TRUNCATED]...\n") + 2500

def test_process_rolling_window_deduplication(mock_agent):
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "tool", "name": "exec", "content": "Result A"},
        {"role": "tool", "name": "exec", "content": "Result A"}, # Duplicate
        {"role": "assistant", "content": "Memory updated..."},  # Meta-chatter
        {"role": "assistant", "content": "Real response"}
    ]
    
    # Note: The method iterates BACKWARDS and keeps the NEWEST unique tool output.
    # And it filters specific assistant phrases.
    
    clean = mock_agent.process_rolling_window(messages, max_tokens=1000)
    
    # Check deduplication
    tool_msgs = [m for m in clean if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"] == "Result A"
    
    # Check meta-chatter filtering
    assist_msgs = [m for m in clean if m["role"] == "assistant"]
    assert len(assist_msgs) == 1
    assert assist_msgs[0]["content"] == "Real response"
