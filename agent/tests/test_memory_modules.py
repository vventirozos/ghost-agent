
import pytest
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY
from ghost_agent.memory.profile import ProfileMemory
from ghost_agent.memory.skills import SkillMemory
from ghost_agent.memory.vector import VectorMemory

# --- ProfileMemory Tests ---

@pytest.fixture
def temp_profile(tmp_path):
    return ProfileMemory(tmp_path)

def test_profile_update_and_load(temp_profile):
    temp_profile.update("personal", "name", "Alice")
    data = temp_profile.load()
    assert data["personal"]["name"] == "Alice"

def test_profile_category_mapping(temp_profile):
    # 'wife' should map to relationships.wife
    temp_profile.update("general", "wife", "Jane")
    data = temp_profile.load()
    assert data["relationships"]["wife"] == "Jane"

def test_profile_delete(temp_profile):
    temp_profile.update("notes", "foo", "bar")
    temp_profile.delete("notes", "foo")
    data = temp_profile.load()
    # Check if category is gone if empty, or just key is gone
    if "notes" in data:
        assert "foo" not in data["notes"]
    else:
        assert "notes" not in data

# --- SkillMemory Tests ---

@pytest.fixture
def temp_skills(tmp_path):
    return SkillMemory(tmp_path)

def test_save_lesson(temp_skills):
    temp_skills.learn_lesson("Task A", "Mistake B", "Solution C")
    playbook = json.loads(temp_skills.file_path.read_text())
    assert len(playbook) == 1
    assert playbook[0]["task"] == "Task A"
    assert playbook[0]["solution"] == "Solution C"

def test_get_playbook_context_fallback(temp_skills):
    temp_skills.learn_lesson("Task A", "Missed semi-colon", "Add ;")
    ctx = temp_skills.get_playbook_context()
    assert "Missed semi-colon" in ctx
    assert "Add ;" in ctx

# --- VectorMemory Tests ---

@pytest.fixture
def mock_chroma():
    with patch("chromadb.PersistentClient") as MockClient:
        mock_client_instance = MagicMock()
        MockClient.return_value = mock_client_instance
        
        mock_collection = MagicMock()
        mock_client_instance.get_or_create_collection.return_value = mock_collection
        # Default behavior: Item not found
        mock_collection.get.return_value = {"ids": []}
        
        yield mock_collection

def test_vector_memory_init(tmp_path, mock_chroma):
    vm = VectorMemory(tmp_path, "http://llm")
    assert vm.collection == mock_chroma
    assert (tmp_path / "library_index.json").exists()

def test_vector_add_simple(tmp_path, mock_chroma):
    vm = VectorMemory(tmp_path, "http://llm")
    vm.add("test memory")
    mock_chroma.add.assert_called_once()
    
    # Verify args
    call_kwargs = mock_chroma.add.call_args.kwargs
    assert call_kwargs["documents"] == ["test memory"]
    assert len(call_kwargs["ids"]) == 1
    assert call_kwargs["metadatas"][0]["type"] == "auto"

def test_vector_search_logic(tmp_path, mock_chroma):
    vm = VectorMemory(tmp_path, "http://llm")
    
    # Mock query return
    mock_chroma.query.return_value = {
        "ids": [["id1"]],
        "documents": [["found doc"]],
        "metadatas": [[{"type": "auto", "timestamp": "2023-01-01"}]],
        "distances": [[0.1]]
    }
    
    result = vm.search("query")
    assert "found doc" in result
    assert "[auto]" in result or "(AUTO)" in result
