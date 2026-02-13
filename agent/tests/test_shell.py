import pytest
import os
import asyncio
from ghost_agent.tools.shell import tool_shell, ShellSession

@pytest.mark.asyncio
async def test_shell_basic():
    res = await tool_shell("echo 'Hello World'")
    assert "Hello World" in res

@pytest.mark.asyncio
async def test_shell_stateful_cwd(tmp_path):
    # 1. Create a directory
    test_dir = tmp_path / "shell_test_dir"
    test_dir.mkdir()
    
    # 2. CD into it
    res_cd = await tool_shell(f"cd {test_dir}")
    assert "Changed directory" in res_cd
    
    # 3. Check PWD
    res_pwd = await tool_shell("pwd")
    assert str(test_dir) in res_pwd.strip()

@pytest.mark.asyncio
async def test_shell_timeout():
    res = await tool_shell("sleep 2", timeout=1)
    assert "timed out" in res.lower()
