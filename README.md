# Ghost Agent: Autonomous modular operator

Optimized for high-performance operation on NVIDIA Jetson Orin Nano 8GB.

## 🧠 Core Architecture
- **System 2 Reasoning**: Multi-turn planning with mandatory checklist enforcement to prevent task skipping.
- **Persistent Skill Acquisition**: Automated "Learn Loop" that records engineering lessons in a persistent JSON playbook.
- **Recursive Self-Correction**: "Stubbornness Guard" blocks identical failed attempts and forces higher-variance brainstorming.

## 🛠️ Hardened Toolset
- **Flexible File System**: Resilient parameter mapping (handles hallucinations of `data`, `text`, `path`, etc.).
- **Safe Execution**: Sandboxed Python/Shell environment with extension enforcement and auto-serialization.
- **Smart Search**: Junk-filtering logic for Tor-proxied deep research (filters SEO spam).

## 🚀 Jetson Optimizations
- **RAM Management**: Explicit `malloc_trim` and context-window pruning (8k ceiling).
- **Local Memory**: ChromaDB with `all-MiniLM-L6-v2` for zero-latency semantic recall.
- **Anti-Bloat Filter**: Aggressive history deduplication keeping only high-value task data.