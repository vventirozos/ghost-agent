
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from ghost_agent.memory.vector import VectorMemory, VectorMemoryError

def test_vector_memory_embedding_failure_exits():
    """
    New behavior: VectorMemory raises VectorMemoryError if embedding model fails to load.
    """
    with patch("chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction") as mock_emb:
        mock_emb.side_effect = ImportError("Fake embedding error")
        
        # This should raise VectorMemoryError
        with pytest.raises(VectorMemoryError) as excinfo:
            VectorMemory(Path("/tmp/ghost_memory"), "http://localhost:8080")
        
        assert "Embedding model failure" in str(excinfo.value)

def test_vector_memory_db_failure_exits():
    """
    New behavior: VectorMemory raises VectorMemoryError if DB client creation fails.
    """
    with patch("chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction"):
        with patch("chromadb.PersistentClient") as mock_client:
            mock_client.side_effect = RuntimeError("Collection already exists")
            
            with pytest.raises(VectorMemoryError) as excinfo:
                VectorMemory(Path("/tmp/ghost_memory"), "http://localhost:8080")
            
            assert "DB Conflict" in str(excinfo.value)
