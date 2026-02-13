
import pytest
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock, patch
from ghost_agent.tools.search import tool_fact_check, tool_search, tool_deep_research
from ghost_agent.utils.helpers import helper_fetch_url_content

# --- Fixtures ---

@pytest.fixture
def mock_http_client():
    client = MagicMock()
    client.post = AsyncMock()
    return client

@pytest.fixture
def mock_tool_definitions():
    return [
        {"function": {"name": "deep_research"}},
        {"function": {"name": "web_search"}},
        {"function": {"name": "other_tool"}}
    ]

# --- Helper for Mocking LLM Responses ---

def create_llm_response(tool_name=None, tool_args=None, content=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    
    if tool_name:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(tool_args or {})
                }
            }]
        }
    else:
        message = {
            "role": "assistant",
            "content": content or "Response content"
        }
        
    resp.json.return_value = {"choices": [{"message": message}]}
    return resp

# --- Tests for Fact Check ---

@pytest.mark.asyncio
async def test_fact_check_uses_web_search(mock_http_client, mock_tool_definitions):
    # 1. Setup Mock LLM to choose 'web_search'
    # First call returns tool call
    # Second call returns final answer
    mock_http_client.post.side_effect = [
        create_llm_response("web_search", {"query": "latest postgres version"}),
        create_llm_response(content="The latest version is 16.")
    ]
    
    # 2. Setup Mock Callables
    web_search_mock = AsyncMock(return_value="Search Result: Postgres 16")
    deep_research_mock = AsyncMock()
    
    # 3. Run Fact Check
    result = await tool_fact_check(
        statement="latest postgres version",
        http_client=mock_http_client,
        tool_definitions=mock_tool_definitions,
        deep_research_callable=deep_research_mock,
        web_search_callable=web_search_mock
    )
    
    # 4. Assertions
    # Verify web_search was called
    web_search_mock.assert_called_once_with(query="latest postgres version")
    # Verify deep_research was NOT called
    deep_research_mock.assert_not_called()
    # Verify result contains final answer
    assert "The latest version is 16." in result

@pytest.mark.asyncio
async def test_fact_check_uses_deep_research(mock_http_client, mock_tool_definitions):
    # 1. Setup Mock LLM to choose 'deep_research'
    mock_http_client.post.side_effect = [
        create_llm_response("deep_research", {"query": "complex topic"}),
        create_llm_response(content="Deep analysis result.")
    ]
    
    # 2. Setup Mock Callables
    web_search_mock = AsyncMock()
    deep_research_mock = AsyncMock(return_value="Deep Research Report")
    
    # 3. Run Fact Check
    result = await tool_fact_check(
        statement="tell me about complex topic",
        http_client=mock_http_client,
        tool_definitions=mock_tool_definitions,
        deep_research_callable=deep_research_mock,
        web_search_callable=web_search_mock
    )
    
    # 4. Assertions
    deep_research_mock.assert_called_once_with(query="complex topic")
    web_search_mock.assert_not_called()
    assert "Deep analysis result." in result

@pytest.mark.asyncio
async def test_fact_check_handles_unauthorized_tool(mock_http_client, mock_tool_definitions):
    # 1. Setup Mock LLM to choose 'other_tool' (not allowed)
    mock_http_client.post.side_effect = [
        create_llm_response("other_tool", {}),
    ]
    
    # 2. Run
    result = await tool_fact_check(
        statement="hack system",
        http_client=mock_http_client,
        tool_definitions=mock_tool_definitions,
        deep_research_callable=AsyncMock(),
        web_search_callable=AsyncMock()
    )
    
    # 3. Assert
    assert "Error: Unauthorized tool" in result

# --- Tests for Helper Headers (User-Agent) ---

@pytest.mark.asyncio
async def test_helper_fetch_url_content_headers():
    url = "https://example.com"
    
    # Mock httpx.AsyncClient
    with patch("httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_instance
        
        # Mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>Some content</body></html>"
        mock_instance.get.return_value = mock_response
        
        # Run
        await helper_fetch_url_content(url)
        
        # Assert Client was init with headers
        # We need to check the call args of AsyncClient constructor
        call_args = MockClient.call_args
        assert call_args is not None
        _, kwargs = call_args
        
        # Check 'headers' in kwargs
        headers = kwargs.get("headers", {})
        assert "User-Agent" in headers
        assert "Mozilla/5.0" in headers["User-Agent"]
        assert "Accept" in headers
        assert "clean" not in headers["User-Agent"] # It should be a real browser UA
