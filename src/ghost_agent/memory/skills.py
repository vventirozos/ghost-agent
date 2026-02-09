import json
import logging
from pathlib import Path
from datetime import datetime
from ..utils.logging import Icons, pretty_log

logger = logging.getLogger("GhostAgent")

class SkillMemory:
    def __init__(self, memory_dir: Path):
        self.file_path = memory_dir / "skills_playbook.json"
        if not self.file_path.exists():
            self.file_path.write_text(json.dumps([]))

    def learn_lesson(self, task: str, mistake: str, solution: str):
        try:
            playbook = json.loads(self.file_path.read_text())
            new_lesson = {
                "timestamp": datetime.now().isoformat(),
                "task": task,
                "mistake": mistake,
                "solution": solution
            }
            # Keep only the last 20 high-value lessons to prevent context bloat
            playbook = [new_lesson] + playbook[:19]
            self.file_path.write_text(json.dumps(playbook, indent=2))
            pretty_log("SKILL ACQUIRED", f"Lesson learned: {task[:30]}...", icon="🎓")
        except Exception as e:
            logger.error(f"Failed to save skill: {e}")

    def get_playbook_context(self) -> str:
        try:
            playbook = json.loads(self.file_path.read_text())
            if not playbook: return "No lessons learned yet."
            
            context = "## RECENT LESSONS LEARNED (Follow these to avoid repeats):\n"
            for i, p in enumerate(playbook[:5]): # Only inject top 5 for efficiency
                context += f"{i+1}. SITUATION: {p['task']}\n   PREVIOUS MISTAKE: {p['mistake']}\n   THE FIX: {p['solution']}\n"
            return context
        except: return ""