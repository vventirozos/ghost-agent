
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from ghost_agent.tools.file_system import tool_read_file, tool_write_file, tool_list_files
from ghost_agent.tools.system import tool_get_weather
from ghost_agent.tools.tasks import tool_schedule_task
from ghost_agent.tools import tasks # import the module to patch the global

# --- File System Tests ---

@pytest.mark.asyncio
async def test_file_system_write_read(tmp_path):
    # Write
    res_w = await tool_write_file("test.txt", "Hello World", tmp_path)
    assert "SUCCESS" in res_w
    assert (tmp_path / "test.txt").read_text() == "Hello World"
    
    # Read
    res_r = await tool_read_file("test.txt", tmp_path)
    assert res_r == "Hello World"

@pytest.mark.asyncio
async def test_file_system_security(tmp_path):
    # Attempt traversal
    res = await tool_read_file("../../../etc/passwd", tmp_path)
    assert "Security Error" in res or "attempts to access outside sandbox" in res

# --- System Tools Tests ---

@pytest.mark.asyncio
async def test_get_weather():
    # Mock httpx
    with patch("httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value.__aenter__.return_value = mock_instance
        
        # Mock geocoding
        mock_instance.get.side_effect = [
            MagicMock(status_code=200, json=lambda: {"results": [{"latitude": 0, "longitude": 0, "name": "TestCity"}]}),
            MagicMock(status_code=200, json=lambda: {"current": {"temperature_2m": 25, "weather_code": 0}}),
        ]
        
        res = await tool_get_weather("sock://proxy", location="TestCity")
        assert "TestCity" in res
        assert "25" in res

# --- Tasks Tests ---

@pytest.mark.asyncio
async def test_schedule_task(tmp_path):
    mock_scheduler = MagicMock()
    mock_memory = MagicMock()
    
    # Patch the global runner in tasks module
    tasks.run_proactive_task_fn = MagicMock()
    
    res = await tool_schedule_task("Task1", "Do it", "interval:60", mock_scheduler, mock_memory)
    
    assert "SUCCESS" in res
    mock_scheduler.add_job.assert_called_once()
    mock_memory.add.assert_called_once()

