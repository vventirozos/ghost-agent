
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from ghost_agent.core.dream import Dreamer

@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.memory_system = MagicMock()
    ctx.memory_system.collection = MagicMock()
    ctx.llm_client = MagicMock()
    ctx.llm_client.chat_completion = AsyncMock()
    return ctx

@pytest.mark.asyncio
async def test_dream_no_memory_system(mock_context):
    mock_context.memory_system = None
    dreamer = Dreamer(mock_context)
    res = await dreamer.dream()
    assert "not available" in res

@pytest.mark.asyncio
async def test_dream_not_enough_entropy(mock_context):
    dreamer = Dreamer(mock_context)
    # Mock search return with few docs
    mock_context.memory_system.collection.get.return_value = {
        "ids": ["1", "2"],
        "documents": ["doc1", "doc2"],
        "metadatas": [{}, {}],
        "embeddings": [[], []]
    }
    
    res = await dreamer.dream()
    assert "Not enough entropy" in res

@pytest.mark.asyncio
async def test_dream_cycle_success(mock_context):
    dreamer = Dreamer(mock_context)
    
    # 1. Mock memory retrieval
    mock_context.memory_system.collection.get.return_value = {
        "ids": ["1", "2", "3", "4"],
        "documents": ["doc1", "doc2", "doc3", "doc4"],
        "metadatas": [{}, {}, {}, {}],
        "embeddings": [[], [], [], []]
    }
    
    # 2. Mock LLM consolidation response
    mock_context.llm_client.chat_completion.return_value = {
        "choices": [{
            "message": {
                "content": '{"consolidations": [{"synthesis": "Unified Fact", "merged_ids": ["1", "2"]}]}'
            }
        }]
    }
    
    res = await dreamer.dream()
    
    # 3. Verify Memory Operations
    # Should add the new fact
    mock_context.memory_system.add.assert_called_with("Unified Fact", {"type": "consolidated_fact", "timestamp": "DREAM_CYCLE"})
    # Should delete old ids
    mock_context.memory_system.collection.delete.assert_called_with(ids=["1", "2"])
    
    assert "Dream Complete" in res
