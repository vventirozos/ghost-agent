# Ghost Agent (Granite 4.0 Micro)

Ghost Agent is an autonomous, high-performance AI system designed to run locally on resource-constrained hardware (specifically optimized for NVIDIA Jetson Nano Orin 8GB). It serves as an intelligent proxy between standard LLM clients (Ollama/OpenAI) and a local llama-server, adding persistent memory, secure code execution, and proactive task capabilities.

## 🚀 Key Features

- **Autonomous Reasoning**: Implements a strict `THOUGHT -> PLAN -> ACTION` protocol optimized for 3B parameter models (Granite 4.0 Micro).
- **Parallel Tool Throughput**: Capable of executing multiple tool calls (search, variables, file I/O) in a single turn.
- **Deterministic Working Memory (Scratchpad)**: Explicitly persist variables across turns to eliminate numerical hallucination.
- **Auto-Eyes Diagnostic**: Injects recursive sandbox tree views into the context upon script failure or specialist mode activation to ensure spatial awareness.
- **Secure Docker Sandbox**: Safely executes Python and Shell scripts in an isolated, supercharged container environment with auto-healing parent directory creation.
- **Pure Anonymous Search**: All web searching and deep research happen over the Tor network using DuckDuckGo (DDGS) for maximum privacy. No external API keys required.
- **Ollama/OpenAI Compatible**: Seamlessly integrates with clients like Msty, Chatbox, or custom API integrations.

## 🛠️ Project Structure

```text
ghost_agent/
├── src/
│   └── ghost_agent/
│       ├── main.py           # Entry point & System Lifecycle
│       ├── api/              # FastAPI App & OpenAI/Ollama Routes
│       ├── core/             # GhostAgent Orchestrator & Logic
│       ├── memory/           # VectorDB, Profile, and Scratchpad
│       ├── sandbox/          # Docker Engine Integration
│       ├── tools/            # Atomic Tool Implementations & Registry
│       └── utils/            # Logging, Token Counting, & Helpers
├── requirements.txt          # System Dependencies
└── README.md                 # Documentation
```

## 📋 Prerequisites

- **OS**: macOS, Linux, or Windows (WSL2).
- **Python**: 3.10 or higher.
- **Infrastructure**:
  - **Docker Desktop** or Engine (must be running).
  - **Upstream LLM**: Ollama or Llama.cpp (Default: `http://127.0.0.1:8080`).
  - **Tor Service**: Required for anonymous search (Default SOCKS5 at `127.0.0.1:9050`).

## 📦 Installation

1. **Clone & Setup**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Sandbox Setup**:
   ```bash
   docker pull python:3.11-slim-bookworm
   ```

3. **Configure Environment** (Optional):
   ```bash
   export GHOST_HOME="~/ghost_agent_data"
   export TOR_PROXY="socks5://127.0.0.1:9050"
   ```

## 🖥️ Usage

Run the agent as a module from the project root:

```bash
python3 -m src.ghost_agent.main [OPTIONS]
```

### CLI Arguments

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--upstream-url` | `http://127.0.0.1:8080` | Upstream LLM provider URL. |
| `--port` | `8000` | Local proxy port. |
| `--smart-memory` | `0.0` | Selectivity threshold (0.1-1.0) for auto-learning. |
| `--anonymous` | `True` | Always use Tor + DuckDuckGo for searches. |
| `--debug` | `False` | Enable verbose logging and full model responses. |

## 🔌 Proactive Operations

Ghost can be scheduled to perform background tasks autonomously. Use the `manage_tasks` tool to create persistent cron-like jobs that trigger investigative or coding workflows without user intervention.

---
**Note**: This project was architected and implemented by AI as an exercise in maximizing the potential of small-parameter models through superior tool design.