import asyncio
import datetime
import json
import logging
import uuid
import re
import gc
import ctypes
import platform
from typing import List, Dict, Any, Optional
from pathlib import Path

from .prompts import SYSTEM_PROMPT, CODE_SYSTEM_PROMPT, SMART_MEMORY_PROMPT, PLANNING_SYSTEM_PROMPT
from .planning import TaskTree, TaskStatus
from ..utils.logging import Icons, pretty_log, request_id_context
from ..utils.token_counter import estimate_tokens
from ..tools.registry import get_available_tools, TOOL_DEFINITIONS
from ..tools.tasks import tool_list_tasks
from ..memory.skills import SkillMemory

logger = logging.getLogger("GhostAgent")

class GhostContext:
    def __init__(self, args, sandbox_dir, memory_dir, tor_proxy):
        self.args = args
        self.sandbox_dir = sandbox_dir
        self.memory_dir = memory_dir
        self.tor_proxy = tor_proxy
        self.llm_client = None
        self.memory_system = None
        self.profile_memory = None
        self.skill_memory = None
        self.scratchpad = None
        self.sandbox_manager = None
        self.scheduler = None
        self.last_activity_time = datetime.datetime.now()
        # [OPTIMIZATION] State Caching for 8GB RAM Targets
        self.cached_sandbox_state = None


class GhostAgent:
    def __init__(self, context: GhostContext):
        self.context = context
        self.available_tools = get_available_tools(context)
        self.agent_semaphore = asyncio.Semaphore(1)
        self.memory_semaphore = asyncio.Semaphore(1) # For background tasks

    def release_unused_ram(self):
        """
        Forces Python and the OS to reclaim unused reserved memory.
        """
        try:
            gc.collect()
            if platform.system() == "Linux":
                try:
                    # Access glibc's malloc_trim directly
                    libc = ctypes.CDLL("libc.so.6")
                    libc.malloc_trim(0)
                except: pass
        except: pass

    def clear_session(self):
        """
        Hard reset of the agent's volatile memory.
        """
        pretty_log("Auto-Flush", "Clearing session memory...", icon=Icons.MEM_SAVE)
        
        # 1. Clear Scratchpad
        if hasattr(self.context, 'scratchpad') and self.context.scratchpad:
            self.context.scratchpad.clear()
            
        # 2. Clear Sandbox Cache
        self.context.cached_sandbox_state = None
        
        # 3. Clear Profile Memory Cache (if implemented)
        # (ProfileMemory usually writes to disk, so no RAM cache to clear unless added later)

        # 4. Trigger Upstream LLM Context Reset
        if self.context.llm_client:
             # We fire and forget this task to avoid blocking
             asyncio.create_task(self.context.llm_client.reset_context())

        # 5. Force Python GC and System Memory Trimming
        self.release_unused_ram()
        pretty_log("Auto-Flush", "RAM released.", icon=Icons.OK)
        return True

    def _prepare_planning_context(self, tools_run_this_turn: List[Dict[str, Any]]) -> str:
        """
        Extracts and truncates the last tool output for the planner.
        Extracted for testability.
        """
        last_tool_output = tools_run_this_turn[-1]["content"] if tools_run_this_turn else "None (Start of Task)"
        # Truncate for prompt efficiency (Target: 5000 chars total)
        if len(last_tool_output) > 5000: 
            last_tool_output = last_tool_output[:2500] + "\n...[TRUNCATED]...\n" + last_tool_output[-2500:]
        return last_tool_output

    def process_rolling_window(self, messages: List[Dict[str, Any]], max_tokens: int) -> List[Dict[str, Any]]:
        if not messages: return []
        system_msgs = [m for m in messages if m.get("role") == "system"]
        raw_history = [m for m in messages if m.get("role") != "system"]
        
        # --- GRANITE-STYLE ANTI-BLOAT FILTER ---
        clean_history = []
        seen_tool_outputs = set()
        
        # We iterate backwards to ensure the MOST RECENT data is always kept
        for msg in reversed(raw_history):
            role = msg.get("role")
            content = str(msg.get("content", ""))
            
            # 1. Deduplicate Tool Outputs (But keep the newest)
            if role == "tool":
                tool_name = msg.get('name', 'unknown')
                fingerprint = f"{tool_name}:{content[:100]}"
                if fingerprint in seen_tool_outputs:
                    continue # Drop older redundant output
                seen_tool_outputs.add(fingerprint)
            
            # 2. Filter Assistant Meta-Chatter
            if role == "assistant":
                lower_content = content.lower()
                if "memory updated" in lower_content or "memory stored" in lower_content:
                    continue
            
            clean_history.append(msg)
        
        clean_history.reverse()

        # Context Compression for remaining long tool outputs
        compressed_history = []
        msg_count = len(clean_history)
        for i, msg in enumerate(clean_history):
            role, content = msg.get("role"), str(msg.get("content", ""))
            # [OPTIMIZATION] Relaxed compression for 8k context (was 2000 chars)
            if role == "tool" and (msg_count - i) > 5 and len(content) > 5000:
                msg["content"] = content[:2000] + "\n... [OLD DATA COMPRESSED] ...\n" + content[-2000:]
            compressed_history.append(msg)

        current_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in system_msgs)
        final_history = []
        for msg in reversed(compressed_history):
            msg_tokens = estimate_tokens(str(msg.get("content", "")))
            if current_tokens + msg_tokens > max_tokens: break
            final_history.append(msg)
            current_tokens += msg_tokens
        final_history.reverse()
        return system_msgs + final_history

    async def run_smart_memory_task(self, interaction_context: str, model_name: str, selectivity: float):
        if not self.context.memory_system: return
        async with self.memory_semaphore:
            interaction_context = interaction_context.replace("\r", "")
            
            # PRE-FILTER: Do not store facts if the user was just asking for a summary or recall
            # This prevents "Ghost Memory" from document summaries
            ic_lower = interaction_context.lower()
            ic_parts = ic_lower.split("ai:")
            user_msg = ic_parts[0] if len(ic_parts) > 0 else ""
            ai_msg = ic_parts[1] if len(ic_parts) > 1 else ""
            
            summary_triggers = ["summarize", "summary", "recall", "tell me about", "what is", "recap", "forget", "list documents"]
            is_requesting_summary = any(w in user_msg for w in summary_triggers)
            
            if is_requesting_summary and len(ai_msg) > 500:
                # Large AI responses to summary/forget requests are likely "Ghost Memory" candidates
                return

            final_prompt = SMART_MEMORY_PROMPT + f"\n{interaction_context}"
            try:
                payload = {"model": model_name, "messages": [{"role": "user", "content": final_prompt}], "stream": False, "temperature": 0.1, "response_format": {"type": "json_object"}}
                data = await self.context.llm_client.chat_completion(payload)
                content = data["choices"][0]["message"]["content"]
                result_json = json.loads(content.replace("```json", "").replace("```", "").strip())
                score, fact, profile_up = float(result_json.get("score", 0.0)), result_json.get("fact", ""), result_json.get("profile_update", None)
                
                # --- AGGRESSIVE SELECTIVITY FILTER ---
                fact_lc = fact.lower()
                is_personal = any(w in fact_lc for w in ["user", "me", "my ", " i ", "identity", "preference", "like"])
                is_technical = any(w in fact_lc for w in ["file", "path", "code", "error", "script", "project", "repo", "build", "library", "version"])
                
                if score >= selectivity and fact and len(fact) <= 200 and len(fact) >= 5 and "none" not in fact_lc:
                    # Discard high scores for facts that are clearly general knowledge (not personal, not technical)
                    if score >= 0.9 and not (is_personal or is_technical):
                        pretty_log("Auto Memory Skip", f"Discarded generic knowledge: {fact}", icon=Icons.STOP)
                        return

                    memory_type = "identity" if (score >= 0.9 and profile_up) else "auto"
                    await asyncio.to_thread(self.context.memory_system.smart_update, fact, memory_type)
                    pretty_log("Auto Memory Store", f"[{score:.2f}] {fact}", icon=Icons.MEM_SAVE)
                    if memory_type == "identity" and self.context.profile_memory:
                        self.context.profile_memory.update(profile_up.get("category", "notes"), profile_up.get("key", "info"), profile_up.get("value", fact))
            except Exception as e: logger.error(f"Smart memory task failed: {e}")

    async def handle_chat(self, body: Dict[str, Any], background_tasks):
        req_id = str(uuid.uuid4())[:8]
        token = request_id_context.set(req_id)
        self.context.last_activity_time = datetime.datetime.now()
        try:
            async with self.agent_semaphore:
                pretty_log("Request Initialized", special_marker="BEGIN")
                messages, model, stream_response = body.get("messages", []), body.get("model", "ghost-agent"), body.get("stream", False)
                
                # HARD HISTORY TRUNCATION: Extended for 8k Context
                # We physically drop everything older than the last 100 messages at entry
                if len(messages) > 100:
                    messages = [m for m in messages if m.get("role") == "system"] + messages[-100:]

                for m in messages:
                    if isinstance(m.get("content"), str): m["content"] = m["content"].replace("\r", "")

                last_user_content = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
                lc = last_user_content.lower()
                
                # VERBATIM INTENT DETECTION FROM ORIGINAL SCRIPT
                coding_keywords = ["python", "bash", "sh", "script", "code", "def ", "import "]
                coding_actions = ["write", "run", "execute", "debug", "fix", "create", "generate", "count", "calculate", "analyze", "scrape", "plot", "graph"]
                has_coding_intent = False
                if any(k in lc for k in coding_keywords):
                    if any(a in lc for a in coding_actions): has_coding_intent = True
                
                # Special cases: Force coding for specific file requests
                if any(x in lc for x in ["execute", "script", ".py"]): has_coding_intent = True
                
                # Trivial check: If it's pure arithmetic without 'python' mentions, don't force specialist mode
                if re.match(r'^[\d\s\+\-\*\/\(\)\=\?]+$', lc):
                    has_coding_intent = False

                profile_context = self.context.profile_memory.get_context_string() if self.context.profile_memory else ""
                profile_context = profile_context.replace("\r", "")

                # Working Memory Injection (Deterministic Persistence)
                # We show this even if empty so the LLM knows the capability exists
                scratch_data = self.context.scratchpad.list_all() if hasattr(self.context, 'scratchpad') else "None."
                working_memory_context = f"\n\n### SCRAPBOOK (Persistent Data):\n{scratch_data}\n\n"

                if has_coding_intent:
                    base_prompt, current_temp = CODE_SYSTEM_PROMPT, 0.2
                    pretty_log("Mode Switch", "Python Specialist Activated", icon=Icons.TOOL_CODE)
                    if profile_context: base_prompt += f"\n\nUSER CONTEXT (For variable naming only):\n{profile_context}"
                else:
                    base_prompt, current_temp = SYSTEM_PROMPT.replace("{{PROFILE}}", profile_context), self.context.args.temperature
                
                base_prompt += working_memory_context
                base_prompt = base_prompt.replace("{{CURRENT_TIME}}", datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')).replace("\r", "")
                
                found_system = False
                for m in messages:
                    if m.get("role") == "system": m["content"] = base_prompt; found_system = True; break
                if not found_system: messages.insert(0, {"role": "system", "content": base_prompt})

                if "task" in lc and ("list" in lc or "show" in lc or "what" in lc or "status" in lc):
                     current_tasks = await tool_list_tasks(self.context.scheduler)
                     messages.append({"role": "system", "content": f"SYSTEM DATA DUMP:\n{current_tasks}\n\nINSTRUCTION: The user cannot see the data above. You MUST copy the task list into your **FINAL ANSWER** now."})

                # VERBATIM MEMORY INJECTION LOGIC FROM ORIGINAL SCRIPT
                is_fact_check = "fact-check" in lc or "verify" in lc
                trivial_triggers = ["who are you", "hello", " hi ", "hey there", "how are you", "what's up", "name is"]
                is_trivial = any(t in last_user_content.lower() for t in trivial_triggers)

                # ONLY inject memory if it's a general chat, NOT trivial, and NOT a fact check
                # Tighten injection: Only trigger on explicit "previous" or "remember" requests when in coding mode
                should_fetch_memory = (
                    not is_fact_check and 
                    not is_trivial and
                    (not has_coding_intent or "remember" in last_user_content or "previous" in last_user_content)
                )

                if self.context.memory_system and last_user_content and should_fetch_memory:
                    mem_context = self.context.memory_system.search(last_user_content)
                    if mem_context:
                        mem_context = mem_context.replace("\r", "")
                        pretty_log("Memory Context", f"Retrieved for: {last_user_content}", icon=Icons.BRAIN_CTX)
                        messages.insert(1, {"role": "system", "content": f"[MEMORY CONTEXT]:\n{mem_context}"})
                
                # --- DYNAMIC SKILL INJECTION ---
                if self.context.skill_memory:
                    playbook = self.context.skill_memory.get_playbook_context(query=last_user_content, memory_system=self.context.memory_system)
                    for m in messages:
                        if m.get("role") == "system":
                            m["content"] += f"\n\n{playbook}"
                            break

                # PHYSICAL PRUNING: We now physically truncate the message list to save RAM
                messages = self.process_rolling_window(messages, self.context.args.max_context)
                
                final_ai_content, created_time = "", int(datetime.datetime.now().timestamp())
                force_stop, seen_tools, tool_usage, last_was_failure = False, set(), {}, False
                raw_tools_called = set() # Track tool names (not fingerprints) for checklist enforcement
                execution_failure_count = 0
                tools_run_this_turn = []
                forget_was_called = False
                thought_content = "" # Persistent for Checklist Enforcement
                was_complex_task = False

                # --- HIERARCHICAL PLANNER INITIALIZATION ---
                task_tree = TaskTree()
                current_plan_json = {}

                for turn in range(20):
                    if turn > 2: was_complex_task = True
                    if force_stop: break
                    
                    # --- SYSTEM 2 ADAPTIVE PLANNING ---
                    # Only run reasoning for complex tasks (Coding or non-trivial requests)
                    if not is_trivial:
                        pretty_log("Reasoning Loop", f"Turn {turn+1} Strategic Analysis...", icon="🧠")
                        
                        # Prepare Planning Context
                        last_tool_output = self._prepare_planning_context(tools_run_this_turn)

                        planning_prompt = f"""
### CURRENT SITUATION
User Request: {last_user_content}
Last Tool Output: {last_tool_output}

### CURRENT PLAN (JSON)
{json.dumps(current_plan_json, indent=2) if current_plan_json else "No plan yet."}
"""

                        planning_payload = {
                            "model": model,
                            "messages": [{"role": "system", "content": PLANNING_SYSTEM_PROMPT}, {"role": "user", "content": planning_prompt}],
                            "temperature": 0.1,
                            "max_tokens": 1024,
                            "response_format": {"type": "json_object"}
                        }
                        
                        try:
                            p_data = await self.context.llm_client.chat_completion(planning_payload)
                            plan_content = p_data["choices"][0]["message"].get("content", "")
                            plan_json = json.loads(plan_content)
                            
                            thought_content = plan_json.get("thought", "No thought provided.")
                            tree_update = plan_json.get("tree_update", {})
                            next_action_id = plan_json.get("next_action_id", "")
                            
                            # Update Tree State
                            if tree_update:
                                task_tree.load_from_json(tree_update)
                                current_plan_json = task_tree.to_json()
                            
                            # Render Tree for System Prompt
                            tree_render = task_tree.render()
                            
                            # Update system message with current thought content
                            found_checklist = False
                            for m in messages:
                                if m.get("role") == "system" and "### ACTIVE STRATEGY" in m.get("content", ""):
                                    m["content"] = f"### ACTIVE STRATEGY & PLAN (DO NOT SKIP ANY STEP):\nTHOUGHT: {thought_content}\n\nPLAN:\n{tree_render}\n\nFOCUS TASK: {next_action_id}"
                                    found_checklist = True
                                    break
                            if not found_checklist:
                                messages.append({"role": "system", "content": f"### ACTIVE STRATEGY & PLAN (DO NOT SKIP ANY STEP):\nTHOUGHT: {thought_content}\n\nPLAN:\n{tree_render}\n\nFOCUS TASK: {next_action_id}"})
                            
                            pretty_log("Reasoning Loop", f"Plan Updated. Focus: {next_action_id}", icon=Icons.OK)
                            
                            # Check for completion
                            if task_tree.root_id and task_tree.nodes[task_tree.root_id].status == TaskStatus.DONE and turn > 0:
                                pretty_log("Finalizing", "Agent signaled completion", icon=Icons.OK)
                                
                                # Enhance final response with the actual result
                                result_context = ""
                                if tools_run_this_turn:
                                    last_out = tools_run_this_turn[-1]["content"]
                                    if len(last_out) > 2000: last_out = last_out[:1000] + "\n...[TRUNCATED]...\n" + last_out[-1000:]
                                    result_context = f"\n\n**Final Result:**\n{last_out}"
                                
                                final_ai_content = f"{thought_content}{result_context}"
                                break

                        except Exception as e:
                            logger.error(f"Planning step failed: {e}")
                            # FALLBACK: If planning fails, still try to proceed with a basic directive
                            if not any("### ACTIVE STRATEGY" in m.get("content", "") for m in messages):
                                messages.append({"role": "system", "content": "### ACTIVE STRATEGY: Proceed with the next logical step to fulfill the user request."})

                    # --- A. DYNAMIC EYES & MEMORY (REAL-TIME CONTEXT) ---
                    # Always refresh Sandbox and Scrapbook state at turn start
                    scratch_data = self.context.scratchpad.list_all() if hasattr(self.context, 'scratchpad') else "None."
                    
                    if has_coding_intent:
                        # [OPTIMIZATION] State Diffing: Only re-scan if files changed
                        # We track file modification via tool usage in the previous turn
                        sandbox_state = self.context.cached_sandbox_state
                        
                        # Check if a file modification tool was used in the PREVIOUS turn
                        # We look at `tools_run_this_turn` which is populated during execution.
                        # Wait, `tools_run_this_turn` is local to the loop. 
                        # We need to track if ANY file-modifying tool ran since last scan.
                        # Let's use a simple heuristic: If cached state is None, scan.
                        # If a tool runs that modifies files, we wipe the cache.
                        
                        if self.context.cached_sandbox_state is None:
                            from ..tools.file_system import tool_list_files
                            params = {
                                "sandbox_dir": self.context.sandbox_dir, 
                                "memory_system": self.context.memory_system
                            }
                            # Use thread pool to avoid blocking the event loop during heavy I/O
                            sandbox_state = await asyncio.to_thread(lambda: asyncio.run(tool_list_files(**params)) if asyncio.iscoroutinefunction(tool_list_files) else tool_list_files(**params))
                            self.context.cached_sandbox_state = sandbox_state
                        else:
                            sandbox_state = self.context.cached_sandbox_state
                        
                        for m in messages:
                            if m.get("role") == "system":
                                # Update Sandbox
                                content = re.sub(r'\n### CURRENT SANDBOX STATE \(Eyes-On\):.*?\n\n', '\n', m["content"], flags=re.DOTALL)
                                content += f"\n### CURRENT SANDBOX STATE (Eyes-On):\n{sandbox_state}\n\n"
                                # Update Scrapbook
                                content = re.sub(r'\n### SCRAPBOOK \(Persistent Data\):.*?\n\n', '\n', content, flags=re.DOTALL)
                                content += f"\n### SCRAPBOOK (Persistent Data):\n{scratch_data}\n\n"
                                m["content"] = content
                                break
                    else:
                        # For non-coding mode, just refresh the Scrapbook
                        for m in messages:
                            if m.get("role") == "system":
                                m["content"] = re.sub(r'\n### SCRAPBOOK \(Persistent Data\):.*?\n\n', '\n', m["content"], flags=re.DOTALL)
                                m["content"] += f"\n### SCRAPBOOK (Persistent Data):\n{scratch_data}\n\n"
                                break

                    # --- B. DYNAMIC TEMPERATURE (ERROR RECOVERY) ---
                    if last_was_failure:
                        # Moderate scaling for execution strikes: 0.20 -> 0.40 -> 0.60
                        if execution_failure_count == 1:
                            active_temp = max(current_temp, 0.40)
                        elif execution_failure_count >= 2:
                            active_temp = max(current_temp, 0.60)
                        else:
                            # General tool failure (non-execution)
                            active_temp = min(current_temp + 0.1, 0.80)
                        
                        pretty_log("Brainstorming", f"Adjusting variance to {active_temp:.2f} to solve error", icon=Icons.IDEA)
                    else:
                        active_temp = current_temp

                    payload = {
                        "model": model, 
                        "messages": messages, 
                        "stream": False, 
                        "tools": TOOL_DEFINITIONS, 
                        "tool_choice": "auto", 
                        "temperature": active_temp, 
                        "frequency_penalty": 0.5,
                        "max_tokens": 4096 # Allow for full script generation
                    }
                    pretty_log("LLM Request", f"Turn {turn+1} | Temp {active_temp:.2f}", icon=Icons.LLM_ASK)
                    
                    try:
                        data = await self.context.llm_client.chat_completion(payload)
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        final_ai_content = "CRITICAL: The upstream LLM server is unreachable. It may have crashed due to memory pressure or is currently restarting. Please wait a moment and try again."
                        pretty_log("System Fault", "Upstream server unreachable", level="ERROR", icon=Icons.FAIL)
                        force_stop = True
                        break
                    except Exception as e:
                        final_ai_content = f"CRITICAL: An unexpected error occurred while communicating with the LLM: {str(e)}"
                        pretty_log("System Fault", str(e), level="ERROR", icon=Icons.FAIL)
                        force_stop = True
                        break

                    msg = data["choices"][0]["message"]
                    content, tool_calls = msg.get("content") or "", msg.get("tool_calls", [])
                    
                    if content:
                        content = content.replace("\r", "")
                        final_ai_content += content
                        msg["content"] = content
                    
                    if not tool_calls:
                        # --- IMPROVED CHECKLIST ENFORCEMENT ---
                        # ONLY check user content for intent, NOT the thought_content (prevents self-triggering)
                        user_request_context = last_user_content.lower()
                        has_meta_intent = any(kw in user_request_context for kw in ["learn", "skill", "profile", "lesson", "playbook", "record", "save"])
                        meta_tools_called = any(t in raw_tools_called for t in ["learn_skill", "update_profile"])
                        
                        if has_meta_intent and not meta_tools_called and turn < 4:
                            pretty_log("Checklist Nudge", "Enforcing meta-task compliance", icon="☝️")
                            # If we are nudging, we should "undo" the final_ai_content addition for this turn 
                            # to prevent repetitive "505050" style outputs.
                            final_ai_content = final_ai_content[:-len(content)] if content else final_ai_content
                            messages.append({"role": "system", "content": "CRITICAL: You have not fulfilled the learning/profile instructions in the user's request. You MUST call 'learn_skill' or 'update_profile' now before finishing."})
                            continue

                        # SKIP Smart Memory if we just performed a 'forget' operation OR if the turn was a failure
                        if self.context.args.smart_memory > 0.0 and last_user_content and not forget_was_called and not last_was_failure:
                            background_tasks.add_task(self.run_smart_memory_task, f"User: {last_user_content}\nAI: {final_ai_content}", model, self.context.args.smart_memory)
                        break
                    
                    messages.append(msg)
                    last_was_failure = False
                    redundancy_strikes = 0
                    
                    tool_tasks, tool_call_metadata = [], []
                    for tool in tool_calls:
                        fname = tool["function"]["name"]
                        raw_tools_called.add(fname)
                        tool_usage[fname] = tool_usage.get(fname, 0) + 1
                        
                        # [OPTIMIZATION] Invalidate Cache on File Modification
                        if fname in ["write_file", "delete_file", "download_file", "git_clone", "unzip", "move_file", "copy_file"]:
                            self.context.cached_sandbox_state = None
                            pretty_log("Context Manager", "File system modified - Cache invalidated", icon="🔄")

                        # Detect forget operation to block smart memory re-learning
                        if fname == "forget":
                            forget_was_called = True
                        elif fname == "knowledge_base":
                            try:
                                args = json.loads(tool["function"]["arguments"])
                                if args.get("action") == "forget":
                                    forget_was_called = True
                            except: pass

                        # Increased limits: 20 for execution, 10 for other hubs
                        if tool_usage[fname] > (20 if fname == "execute" else 10):
                            pretty_log("Loop Breaker", f"Halted overuse: {fname}", icon=Icons.STOP)
                            messages.append({"role": "system", "content": f"SYSTEM: Tool '{fname}' used too many times."})
                            force_stop = True; break
                        try:
                            t_args = json.loads(tool["function"]["arguments"])
                            a_hash = f"{fname}:{json.dumps(t_args, sort_keys=True)}"
                        except: t_args, a_hash = {}, f"{fname}:error"
                        
                        # EXEMPT state-querying tools from redundancy check
                        # This allows listing files multiple times to confirm changes.
                        is_state_tool = fname in ["file_system", "knowledge_base", "web_search", "recall", "list_files", "system_utility", "inspect_file", "manage_tasks"]
                        
                        if a_hash in seen_tools and fname != "execute" and not is_state_tool:
                            redundancy_strikes += 1
                            pretty_log("Redundancy", f"Blocked duplicate: {fname}", icon=Icons.RETRY)
                            messages.append({"role": "tool", "tool_call_id": tool["id"], "name": fname, "content": "SYSTEM MONITOR: Already executed successfully. Do not repeat this tool call. If you have finished all other tasks, provide your final response now."})
                            if redundancy_strikes >= 3: force_stop = True
                            continue # Do NOT set last_was_failure
                        seen_tools.add(a_hash)
                        
                        # --- 👮 CRITIC LOOP (Suggestion 3) ---
                        if fname == "execute":
                            # Intercept execution for complex scripts
                            code_content = t_args.get("content", "")
                            if len(code_content.splitlines()) > 10:
                                pretty_log("Critic Loop", "Reviewing complex code...", icon="🧐")
                                is_approved, revised_code, critique = await self._run_critic_check(code_content, last_user_content, model)
                                
                                if not is_approved and revised_code:
                                    pretty_log("Critic Intervention", "Code was revised by Critic for safety/logic.", icon="🛡️")
                                    t_args["content"] = revised_code
                                    # Update the tool call in history so the LLM sees the *corrected* code
                                    tool["function"]["arguments"] = json.dumps(t_args) 
                                    messages.append({"role": "system", "content": f"CRITIC INTERVENTION: Your code was auto-corrected. \nReason: {critique}\nExecuting revised version."})
                                elif not is_approved:
                                    # Critic rejected but didn't provide code (rare)
                                    messages.append({"role": "tool", "tool_call_id": tool["id"], "name": fname, "content": f"CRITIC BLOCK: {critique}. Please rewrite the code."})
                                    last_was_failure = True
                                    continue

                        if fname in self.available_tools:
                            tool_tasks.append(self.available_tools[fname](**t_args))
                            tool_call_metadata.append((fname, tool["id"], a_hash))
                        else: messages.append({"role": "tool", "tool_call_id": tool["id"], "name": fname, "content": "Error: Unknown tool"})

                    if tool_tasks:
                        results = await asyncio.gather(*tool_tasks, return_exceptions=True)
                        for i, result in enumerate(results):
                            fname, tool_id, a_hash = tool_call_metadata[i]
                            str_res = str(result).replace("\r", "") if not isinstance(result, Exception) else f"Error: {str(result)}"
                            safe_res = str_res[:2000] + "\n...[TRUNCATED]...\n" + str_res[-2000:] if len(str_res) > 4000 else str_res
                            tool_msg = {"role": "tool", "tool_call_id": tool_id, "name": fname, "content": safe_res}
                            messages.append(tool_msg)
                            tools_run_this_turn.append(tool_msg)
                            if fname == "execute":
                                code_match = re.search(r"EXIT CODE:\s*(\d+)", str_res)
                                if code_match:
                                    exit_code_val = int(code_match.group(1))
                                else:
                                    # Fallback: If strict format is missing, check for generic errors
                                    if "Error" in str_res or "Exception" in str_res or "Traceback" in str_res:
                                        exit_code_val = 1
                                    else:
                                        exit_code_val = 0 # Assume success if no error headers found (risky but better than loop)

                                if exit_code_val != 0:
                                    execution_failure_count += 1
                                    last_was_failure = True
                                    
                                    # Show error preview in log
                                    error_preview = "Unknown Error"
                                    if "STDOUT/STDERR:" in str_res:
                                        error_preview = str_res.split("STDOUT/STDERR:")[1].strip().replace("\n", " ")
                                    elif "SYSTEM ERROR:" in str_res:
                                        error_preview = str_res.split("SYSTEM ERROR:")[1].strip().split("\n")[0]
                                    else:
                                        # Emergency Fallback: Just show the start of the result
                                        error_preview = str_res[:60].replace("\n", " ")
                                    
                                    pretty_log("Execution Fail", f"Strike {execution_failure_count}/3 -> {error_preview}", icon=Icons.FAIL)
                                    from ..tools.file_system import tool_list_files
                                    sandbox_state = await tool_list_files(self.context.sandbox_dir, self.context.memory_system)
                                    messages.append({"role": "system", "content": f"AUTO-DIAGNOSTIC: The script failed. SANDBOX TREE:\n{sandbox_state}"})
                                    if execution_failure_count >= 3: force_stop = True
                                else:
                                    execution_failure_count = 0
                                    pretty_log("Execution Ok", "Script completed with exit code 0", icon=Icons.OK)
                                    # If we have meta-tasks pending, DO NOT force stop.
                                    # The Nudge logic will handle the completion.
                                    request_context = (last_user_content + thought_content).lower()
                                    has_meta_intent = any(kw in request_context for kw in ["learn", "skill", "profile", "lesson", "playbook", "record", "save"])
                                    # Removed force_stop for Execute to allow analysis
                                    # if not has_meta_intent:
                                    #     force_stop = True
                            
                            elif str_res.startswith("Error:") or str_res.startswith("Critical Tool Error"):
                                last_was_failure = True
                                if not force_stop: 
                                    # Show the specific error in the log title for better transparency
                                    error_preview = str_res.replace("Error:", "").strip()
                                    pretty_log("Tool Warning", f"{fname} -> {error_preview}", icon=Icons.WARN)
                            
                            elif fname in ["manage_tasks", "learn_skill"] and "SUCCESS" in str_res.upper():
                                # Removed force_stop for meta-tasks to allow confirmation
                                pass
                                # force_stop = True
                
                if not final_ai_content:
                    if tools_run_this_turn: final_ai_content = f"Task completed successfully. Final tool output:\n\n{tools_run_this_turn[-1]['content']}"
                    else: final_ai_content = "Process finished without textual output."
                
                # --- AUTOMATED POST-MORTEM (AUTO-LEARNING) ---
                if was_complex_task or execution_failure_count > 0:
                    try:
                        # Only learn if we actually succeeded in the end
                        if not force_stop or "READY TO FINALIZE" in thought_content.upper():
                            history_summary = f"User: {last_user_content}\n"
                            for t_msg in tools_run_this_turn[-5:]: # Last 5 tool actions
                                history_summary += f"Tool {t_msg['name']}: {t_msg['content'][:200]}\n"
                            
                            learn_prompt = f"### TASK POST-MORTEM\nReview this successful but complex interaction. Did the agent encounter a specific error, hurdle, or mistake that required a unique solution? If so, extract it as a lesson.\n\nHISTORY:\n{history_summary}\n\nFINAL AI: {final_ai_content[:500]}\n\nReturn ONLY a JSON object with 'task', 'mistake', and 'solution'. If no unique lesson is found, return null."
                            
                            payload = {"model": model, "messages": [{"role": "system", "content": "You are a Meta-Cognitive Analyst."}, {"role": "user", "content": learn_prompt}], "temperature": 0.1, "response_format": {"type": "json_object"}}
                            l_data = await self.context.llm_client.chat_completion(payload)
                            l_content = l_data["choices"][0]["message"].get("content", "")
                            if l_content and "null" not in l_content.lower():
                                l_json = json.loads(l_content)
                                if all(k in l_json for k in ["task", "mistake", "solution"]):
                                    self.context.skill_memory.learn_lesson(l_json["task"], l_json["mistake"], l_json["solution"], memory_system=self.context.memory_system)
                                    pretty_log("Auto-Learning", "New lesson captured automatically", icon="🎓")
                    except Exception as e:
                        logger.error(f"Auto-learning failed: {e}")

                return final_ai_content, created_time, req_id
        finally:
            # [OPTIMIZATION] Aggressive Garbage Collection
            if 'messages' in locals(): del messages
            if 'tools_run_this_turn' in locals(): del tools_run_this_turn
            if 'sandbox_state' in locals(): del sandbox_state
            if 'data' in locals(): del data
            
            self.release_unused_ram()
            
            pretty_log("Request Finished", special_marker="END")
            request_id_context.reset(token)

    async def _run_critic_check(self, code: str, task_context: str, model: str):
        """
        Internal loop to critique code before execution.
        Returns: (is_approved: bool, revised_code: str | None, critique: str)
        """
        from .prompts import CRITIC_SYSTEM_PROMPT
        try:
            prompt = f"### USER TASK:\n{task_context}\n\n### PROPOSED CODE:\n{code}"
            payload = {
                "model": model, 
                "messages": [{"role": "system", "content": CRITIC_SYSTEM_PROMPT}, {"role": "user", "content": prompt}], 
                "temperature": 0.0,
                "response_format": {"type": "json_object"}
            }
            data = await self.context.llm_client.chat_completion(payload)
            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
            
            if result.get("status") == "APPROVED":
                return True, None, "Approved"
            else:
                return False, result.get("revised_code"), result.get("critique", "Unspecified issue")
                
        except Exception as e:
            logger.error(f"Critic failed: {e}")
            return True, None, "Critic Failed (Fail-Open)"


