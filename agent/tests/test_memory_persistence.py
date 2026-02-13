import pytest
import shutil
import json
from pathlib import Path
from ghost_agent.memory.vector import VectorMemory, VectorMemoryError
from ghost_agent.memory.profile import ProfileMemory

# Mock the embedding function to avoid calling the real LLM/Chroma during unit tests
class MockEmbeddingFunction:
    def __call__(self, input):
        # Return a fixed 384-dim vector for any input (simulating all-MiniLM-L6-v2)
        return [[0.1] * 384 for _ in input]

@pytest.fixture
def memory_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    return d

@pytest.fixture
def mock_vector_memory(memory_dir, monkeypatch):
    # Patch the embedding function in vector.py
    # We need to patch where it is IMPORTED or USED
    
    # 1. Prevent real Chroma client from trying to load the real model if we can
    # But VectorMemory init tries to load sentence-transformers. 
    # Let's mock the internal _init_ of VectorMemory to inject our mock function
    # OR better, let's just let it run if the environment has the model, 
    # but for CI/speed we should mock it.
    
    # For now, let's try to verify persistence of the DB itself.
    # If we assume the environment has the dependencies installed (which it does).
    
    vm = VectorMemory(memory_dir, upstream_url="http://mock-url")
    # Force replace the embedding function with a fast mock
    vm.collection._embedding_function = MockEmbeddingFunction()
    return vm

def test_profile_memory_persistence(memory_dir):
    # 1. specific file path
    pm = ProfileMemory(memory_dir)
    pm.update("personal", "name", "Alice")
    pm.update("interests", "hobby", "Coding")
    
    # 2. Reload from disk
    pm2 = ProfileMemory(memory_dir)
    data = pm2.load()
    
    assert data["personal"]["name"] == "Alice"
    assert data["interests"]["hobby"] == "Coding"

def test_vector_memory_persistence(memory_dir):
    # 1. Initialize and Add
    vm = VectorMemory(memory_dir, upstream_url="http://mock-url")
    # Inject mock embedding to speed up and avoid network
    vm.collection._embedding_function = MockEmbeddingFunction()
    
    vm.add("The capital of France is Paris.", {"type": "fact", "timestamp": "2023-01-01"})
    vm.add("Python is a programming language.", {"type": "fact", "timestamp": "2023-01-02"})
    
    # Force commit/save (Chroma is usually auto-persisting, but let's be sure)
    # Chroma 0.4+ persists automatically on write.
    del vm # Force cleanup
    
    # 2. Reload
    vm2 = VectorMemory(memory_dir, upstream_url="http://mock-url")
    vm2.collection._embedding_function = MockEmbeddingFunction()
    
    assert vm2.collection.count() == 2
    
    # 3. Verify Search (Recall)
    # We expect "France" to retrieve the first fact. 
    # Since we use a MockEmbedding where all vectors are identical, 
    # semantic search won't distinguish well. 
    # So we mainly verify that data IS there.
    
    results = vm2.collection.get() # Get all
    docs = results['documents']
    assert "The capital of France is Paris." in docs
    assert "Python is a programming language." in docs

def test_library_index(memory_dir):
    vm = VectorMemory(memory_dir, upstream_url="http://mock-url")
    vm.collection._embedding_function = MockEmbeddingFunction()
    
    # Ingest mock
    vm.ingest_document("test.txt", ["Chunk 1", "Chunk 2"])
    
    # Check index
    details = vm.get_library()
    assert "test.txt" in details
    
    # Reload
    vm2 = VectorMemory(memory_dir, upstream_url="http://mock-url")
    details2 = vm2.get_library()
    assert "test.txt" in details2
