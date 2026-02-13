
from typing import Literal, Optional
from pydantic import BaseModel, Field

class SystemUtility(BaseModel):
    """MANDATORY for Real-Time Data. Use this to check the current time, system health/status, user location, or get the weather. You DO NOT have access to these values without this tool."""
    action: Literal["check_time", "check_weather", "check_health", "check_location"]
    location: Optional[str] = Field(None, description="Required ONLY for 'check_weather'. Specify the city name (e.g., 'Paris'). Leave empty for local weather.")

class FileSystem(BaseModel):
    """Unified file manager. Use this to list, read, write, or download files."""
    operation: Literal["list", "read", "write", "download", "search", "inspect", "move"]
    path: Optional[str] = Field(None, description="The target filename (e.g., 'app.log'). MANDATORY for write/read/inspect. Optional for download. Source for move.")
    content: Optional[str] = Field(None, description="The text to write (MANDATORY for operation='write').")
    url: Optional[str] = Field(None, description="The URL to download (MANDATORY for operation='download').")
    destination: Optional[str] = Field(None, description="The destination filename (MANDATORY for operation='move').")

class KnowledgeBase(BaseModel):
    """Unified memory manager (ingest_document, forget, list_docs, reset_all)."""
    action: Literal["ingest_document", "forget", "list_docs", "reset_all"]
    content: Optional[str] = None

class Recall(BaseModel):
    """Search long-term memory for facts, discussions, or document content."""
    query: str

class Execute(BaseModel):
    """Run Python or Shell code in a secure sandbox. ALWAYS print results."""
    filename: str
    content: str

class LearnSkill(BaseModel):
    """MANDATORY when you solve a complex bug or task after initial failure. Save the lesson so you don't repeat the mistake."""
    task: str
    mistake: str
    solution: str

class WebSearch(BaseModel):
    """Search the internet (Anonymous via Tor)."""
    query: str

class DeepResearch(BaseModel):
    """Performs deep analysis by searching multiple sources and synthesizing a report."""
    query: str

class FactCheck(BaseModel):
    """Verify a claim using deep research and external sources."""
    statement: str

class UpdateProfile(BaseModel):
    """Save a permanent fact about the user (name, preferences, location)."""
    category: Literal["root", "projects", "notes", "relationships"]
    key: str
    value: str

class ManageTasks(BaseModel):
    """Consolidated task manager (create, list, stop, stop_all)."""
    action: Literal["create", "list", "stop", "stop_all"]
    task_name: Optional[str] = None
    cron_expression: Optional[str] = None
    prompt: Optional[str] = None
    task_identifier: Optional[str] = None

class ReadWebPage(BaseModel):
    """Reads the full content of a specific URL. Use this to dive deeper into search results."""
    url: str

class Shell(BaseModel):
    """Executes a shell command in a persistent session (supports cd)."""
    command: str
    timeout: Optional[int] = 10

class DreamMode(BaseModel):
    """Triggers Active Memory Consolidation. Use this when the user asks to 'sleep', 'rest', or 'consolidate memories'."""
    pass

class Replan(BaseModel):
    """Call this tool if your current strategy is failing or if you need to pause and rethink. It forces a fresh planning step."""
    reason: str = Field(..., description="Why are you replanning?")
