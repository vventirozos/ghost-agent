import asyncio
import datetime
import json
import logging
import uuid
import re
from typing import List, Dict, Any, Optional
from pathlib import Path

from .prompts import SYSTEM_PROMPT, CODE_SYSTEM_PROMPT, SMART_MEMORY_PROMPT
from ..utils.logging import Icons, pretty_log, request_id_context
from ..utils.token_counter import estimate_tokens
from ..tools.registry import get_available_tools, TOOL_DEFINITIONS
from ..tools.tasks import tool_list_tasks

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
        self.scratchpad = None
        self.sandbox_manager = None
        self.scheduler = None

class GhostAgent:
    def __init__(self, context: GhostContext):
        self.context = context
        self.available_tools = get_available_tools(context)
        self.agent_semaphore = asyncio.Semaphore(1)

    def process_rolling_window(self, messages: List[Dict[str, Any]], max_tokens: int) -> List[Dict[str, Any]]:
        if not messages: return []
        system_msgs = [m for m in messages if m.get("role") == "system"]
        raw_history = [m for m in messages if m.get("role") != "system"]
        
        # Context Compression
        compressed_history = []
        msg_count = len(raw_history)
        for i, msg in enumerate(raw_history):
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
        interaction_context = interaction_context.replace("\r", "")
        final_prompt = SMART_MEMORY_PROMPT + f"\n{interaction_context}"
        try:
            payload = {"model": model_name, "messages": [{"role": "user", "content": final_prompt}], "stream": False, "temperature": 0.1, "response_format": {"type": "json_object"}}
            data = await self.context.llm_client.chat_completion(payload)
            content = data["choices"][0]["message"]["content"]
            result_json = json.loads(content.replace("```json", "").replace("```", "").strip())
            score, fact, profile_up = float(result_json.get("score", 0.0)), result_json.get("fact", ""), result_json.get("profile_update", None)
            if score >= selectivity and fact and len(fact) <= 200 and len(fact) >= 5 and "none" not in fact.lower():
                memory_type = "identity" if (score >= 0.9 and profile_up) else "auto"
                await asyncio.to_thread(self.context.memory_system.smart_update, fact, memory_type)
                pretty_log(f"Auto Memory Store [{score:.2f}]", fact, icon=Icons.MEM_SAVE)
                if memory_type == "identity" and self.context.profile_memory:
                    self.context.profile_memory.update(profile_up.get("category", "notes"), profile_up.get("key", "info"), profile_up.get("value", fact))
        except Exception as e: logger.error(f"Smart memory task failed: {e}")

    async def handle_chat(self, body: Dict[str, Any], background_tasks):
        req_id = str(uuid.uuid4())[:8]
        token = request_id_context.set(req_id)
        try:
            async with self.agent_semaphore:
                pretty_log("Request Initialized", special_marker="BEGIN")
                messages, model, stream_response = body.get("messages", []), body.get("model", "ghost-agent"), body.get("stream", False)
                
                for m in messages:
                    if isinstance(m.get("content"), str): m["content"] = m["content"].replace("\r", "")

                last_user_content = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
                lc = last_user_content.lower()
                
                # VERBATIM INTENT DETECTION FROM ORIGINAL SCRIPT
                coding_keywords = ["python", "bash", "sh", "script", "code", "def ", "import "]
                coding_actions = ["write", "run", "execute", "debug", "fix", "create", "generate"] # Removed 'count' to prevent math loops
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
                scratch_data = self.context.scratchpad.list_all() if hasattr(self.context, 'scratchpad') else ""
                working_memory_context = f"\n\n### WORKING MEMORY (Saved Variables for Code):\n{scratch_data}\n\n" if scratch_data and "empty" not in scratch_data.lower() else ""

                if has_coding_intent:
                    base_prompt, current_temp = CODE_SYSTEM_PROMPT, 0.2
                    pretty_log("Strategy Switch", "Activated: Python Specialist Mode", level="INFO", icon=Icons.TOOL_CODE)
                    if profile_context: base_prompt += f"\n\nUSER CONTEXT (For variable naming only):\n{profile_context}"
                    
                    # AUTO-EYES: Always show the sandbox tree to the Python Specialist
                    from ..tools.file_system import tool_list_files
                    sandbox_state = await tool_list_files(self.context.sandbox_dir, self.context.memory_system)
                    base_prompt += f"\n\n### CURRENT SANDBOX STATE (Eyes-On):\n{sandbox_state}\n"
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
                trivial_triggers = ["time", "date", "weather", "who are you", "system health", "status", "name is", "hello", " hi "]
                is_trivial = any(t in last_user_content.lower() for t in trivial_triggers)

                # ONLY inject memory if it's a general chat, NOT trivial, and NOT a fact check
                should_fetch_memory = (
                    not is_fact_check and 
                    not is_trivial and
                    (not has_coding_intent or "remember" in last_user_content or "previous" in last_user_content or "recall" in last_user_content)
                )

                if self.context.memory_system and last_user_content and should_fetch_memory:
                    mem_context = self.context.memory_system.search(last_user_content)
                    if mem_context:
                        mem_context = mem_context.replace("\r", "")
                        pretty_log("Memory Injection", f"Retrieved context for: {last_user_content[:30]}...", icon=Icons.BRAIN_CTX)
                        messages.insert(1, {"role": "system", "content": f"[MEMORY CONTEXT]:\n{mem_context}"})
                
                messages = self.process_rolling_window(messages, self.context.args.max_context)
                final_ai_content, created_time = "", int(datetime.datetime.now().timestamp())
                force_stop, seen_tools, tool_usage, last_was_failure = False, set(), {}, False
                execution_failure_count = 0
                tools_run_this_turn = []

                for turn in range(20):
                    if force_stop: break
                    
                    # --- A. DYNAMIC TEMPERATURE (ERROR RECOVERY) ---
                    if last_was_failure:
                        boost = 0.3 if has_coding_intent else 0.2
                        active_temp = min(current_temp + boost, 0.95)
                        pretty_log("Brainstorming", f"Increasing variance to {active_temp:.2f} to solve error", icon=Icons.IDEA)
                    else:
                        active_temp = current_temp

                    payload = {"model": model, "messages": messages, "stream": False, "tools": TOOL_DEFINITIONS, "tool_choice": "auto", "temperature": active_temp, "frequency_penalty": 0.5}
                    pretty_log("LLM Request", f"Turn {turn+1} | Temp {active_temp:.2f}", icon=Icons.LLM_ASK)
                    data = await self.context.llm_client.chat_completion(payload)
                    msg = data["choices"][0]["message"]
                    content, tool_calls = msg.get("content") or "", msg.get("tool_calls", [])
                    
                    if content:
                        content = content.replace("\r", "")
                        final_ai_content += content
                        msg["content"] = content
                    
                    if not tool_calls:
                        if self.context.args.smart_memory > 0.0 and last_user_content:
                            background_tasks.add_task(self.run_smart_memory_task, f"User: {last_user_content}\nAI: {final_ai_content}", model, self.context.args.smart_memory)
                        break
                    
                    messages.append(msg)
                    last_was_failure = False
                    redundancy_strikes = 0
                    
                    tool_tasks, tool_call_metadata = [], []
                    for tool in tool_calls:
                        fname = tool["function"]["name"]
                        tool_usage[fname] = tool_usage.get(fname, 0) + 1
                        
                        # Increased limits: 20 for execution, 10 for other hubs
                        if tool_usage[fname] > (20 if fname == "execute" else 10):
                            pretty_log("Loop Breaker", f"Halted tool overuse: {fname}", icon=Icons.STOP)
                            messages.append({"role": "system", "content": f"SYSTEM: Tool '{fname}' used too many times."})
                            force_stop = True; break
                        try:
                            t_args = json.loads(tool["function"]["arguments"])
                            a_hash = f"{fname}:{json.dumps(t_args, sort_keys=True)}"
                        except: t_args, a_hash = {}, f"{fname}:error"
                        if a_hash in seen_tools and fname != "execute":
                            redundancy_strikes += 1
                            pretty_log("Redundancy", f"Blocked duplicate call: {fname}", icon=Icons.RETRY)
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
                                        error_preview = str_res.split("STDOUT/STDERR:")[1].strip()[:50].replace("\n", " ")
                                    
                                    pretty_log("Exec Failure", f"Strike {execution_failure_count}/3 -> {error_preview}...", icon=Icons.FAIL)
                                    from ..tools.file_system import tool_list_files
                                    sandbox_state = await tool_list_files(self.context.sandbox_dir, self.context.memory_system)
                                    messages.append({"role": "system", "content": f"AUTO-DIAGNOSTIC: The script failed. SANDBOX TREE:\n{sandbox_state}"})
                                    if execution_failure_count >= 3: force_stop = True
                                else:
                                    execution_failure_count = 0
                                    pretty_log("Exec Success", "Script completed with exit code 0", icon=Icons.OK)
                                    force_stop = True
                            
                            elif str_res.startswith("Error:") or str_res.startswith("Critical Tool Error"):
                                last_was_failure = True
                                if not force_stop: 
                                    # Show the specific error in the log title for better transparency
                                    error_preview = str_res.replace("Error:", "").strip()[:50]
                                    pretty_log("Tool Alert", f"{fname} -> {error_preview}...", icon=Icons.WARN)
                            
                            if fname in ["manage_tasks"] and "SUCCESS" in str_res: force_stop = True
                
                if not final_ai_content:
                    if tools_run_this_turn: final_ai_content = f"Task completed successfully. Final tool output:\n\n{tools_run_this_turn[-1]['content']}"
                    else: final_ai_content = "Process finished without textual output."
                return final_ai_content, created_time, req_id
        finally:
            pretty_log("Request Finished", special_marker="END")
            request_id_context.reset(token)
