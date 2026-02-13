
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, AsyncMock, patch
from ghost_agent.api.app import create_app
from ghost_agent.core.agent import GhostAgent, GhostContext

@pytest.fixture
def mock_agent():
    mock_ctx = MagicMock(spec=GhostContext)
    mock_ctx.llm_client = MagicMock()
    mock_ctx.llm_client.http_client = AsyncMock()
    mock_ctx.args = MagicMock()
    mock_ctx.args.api_key = "secret-token"
    
    agent = MagicMock(spec=GhostAgent)
    agent.context = mock_ctx
    agent.handle_chat = AsyncMock(return_value=("Response Content", 1234567890, "req_id_123"))
    return agent

@pytest.fixture
def client(mock_agent):
    app = create_app()
    app.state.agent = mock_agent
    return TestClient(app)

def test_root_endpoints(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.text == "Ollama is running"

def test_api_version(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    assert "version" in r.json()

def test_chat_completions_no_auth(client):
    # Should fail without API key
    payload = {"model": "ghost", "messages": [{"role": "user", "content": "hi"}]}
    r = client.post("/v1/chat/completions", json=payload)
    assert r.status_code == 403

def test_chat_completions_success(client, mock_agent):
    payload = {"model": "ghost", "messages": [{"role": "user", "content": "hi"}]}
    headers = {"X-Ghost-Key": "secret-token"}
    
    r = client.post("/v1/chat/completions", json=payload, headers=headers)
    
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "Response Content"
    mock_agent.handle_chat.assert_called_once()
