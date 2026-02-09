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

from .prompts import SYSTEM_PROMPT, CODE_SYSTEM_PROMPT, SMART_MEMORY_PROMPT
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
        if hasattr(self.context, 'scratchpad') and self.context.scratchpad:
            self.context.scratchpad.clear()
        self.release_unused_ram()
        return True

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
                if "memory has been updated" in lower_content or "memory stored" in lower_content:
                    continue
            
            clean_history.append(msg)
        
        clean_history.reverse()

        # Context Compression for remaining long tool outputs
        compressed_history = []
        msg_count = len(clean_history)
        for i, msg in enumerate(clean_history):
            role, content = msg.get("role"), str(msg.get("content", ""))
            if role == "tool" and (msg_count - i) > 5 and len(content) > 2000:
                msg["content"] = content[:1000] + "\n... [OLD DATA COMPRESSED] ...\n" + content[-1000:]
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
                
                # HARD HISTORY TRUNCATION: Prevent client-side history bloat from eating RAM
                # We physically drop everything older than the last 50 messages at entry
                if len(messages) > 50:
                    messages = [m for m in messages if m.get("role") == "system"] + messages[-50:]

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
                    playbook = self.context.skill_memory.get_playbook_context()
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

                for turn in range(20):
                    if force_stop: break
                    
                    # --- SYSTEM 2 REASONING LOOP ---
                    # Only run reasoning for complex tasks (Coding or non-trivial requests)
                    if turn == 0 and not is_trivial:
                        reasoning_payload = {
                            "model": model,
                            "messages": messages + [{"role": "user", "content": "THINK: Review the objective and any recent lessons. Create a STAMPED CHECKLIST of every single action EXPLICITLY requested by the user. Identify if the user specifically asked for meta-tasks (like learning a skill or recording a lesson). If the request is a simple greeting or statement, do NOT invent extra tasks. Do not use tools yet."}],
                            "temperature": 0.1,
                            "max_tokens": 512
                        }
                        try:
                            pretty_log("Reasoning Loop", "Starting System 2 Analysis...", icon="🧠")
                            r_data = await self.context.llm_client.chat_completion(reasoning_payload)
                            thought_content = r_data["choices"][0]["message"].get("content", "")
                            # Inject thought as a LATE system message so it's fresh in 'attention'
                            messages.append({"role": "system", "content": f"### ACTIVE STRATEGY & CHECKLIST (DO NOT SKIP ANY STEP):\n{thought_content}"})
                            pretty_log("Reasoning Loop", "Analysis complete", icon=Icons.OK)
                        except: pass

                    # --- A. DYNAMIC EYES & MEMORY (REAL-TIME CONTEXT) ---
                    # Always refresh Sandbox and Scrapbook state at turn start
                    scratch_data = self.context.scratchpad.list_all() if hasattr(self.context, 'scratchpad') else "None."
                    
                    if has_coding_intent:
                        from ..tools.file_system import tool_list_files
                        sandbox_state = await tool_list_files(self.context.sandbox_dir, self.context.memory_system)
                        
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
                            messages.append({"role": "tool", "tool_call_id": tool["id"], "name": fname, "content": "SYSTEM MONITOR: Already executed. Move to next step."})
                            if redundancy_strikes >= 3: force_stop = True
                            last_was_failure = True; continue
                        seen_tools.add(a_hash)
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
                                exit_code_val = int(code_match.group(1)) if code_match else 1
                                if exit_code_val != 0:
                                    execution_failure_count += 1
                                    last_was_failure = True
                                    
                                    # Show error preview in log
                                    error_preview = "Unknown Error"
                                    if "STDOUT/STDERR:" in str_res:
                                        error_preview = str_res.split("STDOUT/STDERR:")[1].strip().replace("\n", " ")
                                    
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
                                    if not has_meta_intent:
                                        force_stop = True
                            
                            elif str_res.startswith("Error:") or str_res.startswith("Critical Tool Error"):
                                last_was_failure = True
                                if not force_stop: 
                                    # Show the specific error in the log title for better transparency
                                    error_preview = str_res.replace("Error:", "").strip()
                                    pretty_log("Tool Warning", f"{fname} -> {error_preview}", icon=Icons.WARN)
                            
                            if fname in ["manage_tasks"] and "SUCCESS" in str_res: force_stop = True
                
                if not final_ai_content:
                    if tools_run_this_turn: final_ai_content = f"Task completed successfully. Final tool output:\n\n{tools_run_this_turn[-1]['content']}"
                    else: final_ai_content = "Process finished without textual output."
                
                # Cleanup volatile objects
                del tools_run_this_turn
                del messages
                self.release_unused_ram()
                
                return final_ai_content, created_time, req_id
        finally:
            pretty_log("Request Finished", special_marker="END")
            request_id_context.reset(token)
