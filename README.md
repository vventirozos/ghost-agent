# 👻 Ghost Agent: Autonomous Modular Operator

> **Optimized for High-Performance Operation on Edge Devices specifically designed for NVIDIA Jetson nano 8GB **

Ghost Agent is an autonomous AI operator designed to execute complex coding, research, and system administration tasks with minimal human intervention. It combines **System 2 reasoning** skills with a robust **execution environment** to solve problems iteratively, learn from mistakes, and manage its own memory.

---
Latest release: the agent no longer uses Granite-4-micro as default LLM , it has been aligned with Huihui-Qwen3-4B-Instruct-2507-abliterated.Q4_K_M .
This change showed very good results in performance (capability) and memory management.
---

## 🧠 Core Intelligence

### 1. System 2 Reasoning & Planning
Unlike standard chatbots, Ghost Agent employs a hierarchical planner (`TaskTree`) to break down complex objectives into manageable steps.
- **Dynamic Replanning**: The agent continuously evaluates its progress. If a strategy fails, it brainstorms alternative approaches (adjusting "temperature" variance).
- **Loop Control**: Strict checklist enforcement prevents premature stops, while intelligent termination logic ensures it stops exactly when the job is done (no "runaway" loops).

### 2. Robust Code Execution
The agent features a battle-tested **Python Specialist** mode:
- **Sanitizer 2.0**: A sophisticated heuristic engine that repairs broken LLM code output (e.g., fixing "mashed" imports like `import os\nn`, unclosed strings, and hallucinated line continuations) *before* execution.
- **Sandboxed Runtime**: Executes code in a controlled environment to prevent system damage.
- **Persistent Shell**: Maintains a stateful terminal session (`tool_shell`) for navigating directories and running long-lived commands.
- **Auto-Correction**: If a script fails (non-zero exit code), the agent analyzes the stderr output and attempts to fix the code automatically, up to 3 times per turn.

### 3. Persistent Memory Matrix
Ghost Agent remembers you and your project context:
- **Smart Memory**: Automatically extracts and stores facts, preferences, and project details in a vector-searchable database.
- **Profile Memory**: Maintains a structured user profile (identity, coding style preferences).
- **Skill Memory**: Records "Playbooks" and "Lessons" from successful complex tasks. If the agent solves a tricky error, it saves the solution to avoid repeating the mistake in the future.

### 4. Edge Optimization
Built to run efficiently on limited hardware (e.g., 8GB RAM devices):
- **Aggressive Garbage Collection**: Explicit `malloc_trim` calls to release memory back to the OS.
- **Context Pruning**: Intelligent history compression that keeps relevant context while discarding "bloat".
- **Local Vectors**: Uses `ChromaDB` with lightweight models for zero-latency recall.

---

## 🛠️ Tool Ecosystem

The agent has access to a powerful suite of tools:

| Category | Tools | Capabilities |
|----------|-------|--------------|
| **File System** | `read`, `write`, `list`, `move`, `rename` | Full CRUD operations with auto-correction for recursive listings and binary file handling. |
| **Terminal** | `tool_shell`, `execute` | Stateful bash commands and isolated Python script execution. |
| **Web** | `web_search`, `read_web_page` | Tor-proxied searching (junk-filtered) and deep content extraction. |
| **System** | `check_health`, `manage_tasks` | Self-diagnostics (Docker/Network/Disk) and meta-task management. |
| **Memory** | `recall`, `learn_skill` | Active memory retrieval and explicit lesson recording. |

---

## 🏗️ Architecture Flow

1.  **Input**: User request is received.
2.  **Context Loading**: Relevant memories and skills are retrieved from the Knowledge Base.
3.  **Planning (System 2)**: The Planner analyzes the state and updates the `TaskTree`.
4.  **Execution (System 1)**: The LLM selects tools to execute the immediate next step.
    *   *Sanitization*: Code is cleaned and validated.
    *   *Critic Check*: Complex code is reviewed for safety.
5.  **Observation**: Tool outputs (`stdout`, `stderr`) are captured.
6.  **Loop**: The cycle repeats until the Planner signals `TaskStatus.DONE`.
7.  **Auto-Learning**: If the task was novel/complex, a lesson is synthesized and stored.

---

## 🚀 Getting Started

### Prerequisites
- Linux Environment (Ubuntu 22.04+ recommended)
- Python 3.10+
- OpenAI API Key (or compatible LLM endpoint)

### Installation
```bash
# Clone the repository
git clone https://github.com/ghost-agent/core.git
cd ghost-agent


# Install dependencies
pip install -r requirements.txt
```

### Usage
Run the main agent loop:
```bash
python -m ghost_agent.main
```

### Running Tests
The project maintains a rigorous test suite to ensure stability.
```bash
# Run all tests (async supported)
pytest tests/

# Run specific regression tests
pytest tests/test_mashed_newlines.py  # Verifies sanitizer robustness
pytest tests/test_system.py           # Verifies system tools
```

---

## 🛡️ Stability & reliability
Recent improvements (Feb 2026) have hardened the agent against common failure modes:
- **Sanitizer Refinement**: Fixed persistent `SyntaxError` issues caused by LLM escaping (e.g., preserving `\n` in strings while fixing it in code blocks).
- **Asyncio Stability**: Resolved `Event loop is closed` warnings in sub-process management.
- **Flow Control**: Eliminated "runaway" loops by enforcing strict Planner termination signals.

---

**License**: MIT
**Author**: EvolMonkey