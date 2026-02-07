import asyncio
import hashlib
import os
from pathlib import Path
from typing import List
from ..utils.logging import Icons, pretty_log
from ..utils.helpers import get_utc_timestamp, helper_fetch_url_content, recursive_split_text
from ..memory.scratchpad import Scratchpad

async def tool_remember(text: str, memory_system):
    pretty_log("Memory Manual Store", text[:50], icon=Icons.MEM_SAVE)
    if not memory_system: return "Error: Memory system not active."
    try:
        meta = {"timestamp": get_utc_timestamp(), "type": "manual"}
        await asyncio.to_thread(memory_system.add, text, meta)
        return f"Memory stored: '{text}'"
    except Exception as e:
        return f"Error storing memory: {e}"

async def tool_gain_knowledge(filename: str, sandbox_dir: Path, memory_system):
    import time
    import fitz  # PyMuPDF

    if len(filename) > 2000 or "\n" in filename:
        return "Error: Input contains newlines or is too long."

    pretty_log("Knowledge Ingestion", filename, icon=Icons.MEM_INGEST)
    if not memory_system: return "Error: Memory system is disabled."

    current_library = memory_system.get_library()
    if filename in current_library:
        return f"Skipped: '{filename}' is already in KB."

    full_text = ""
    is_web = filename.lower().startswith("http://") or filename.lower().startswith("https://")

    if is_web:
        pretty_log("Fetching URL", filename, icon=Icons.TOOL_DOWN)
        try:
            full_text = await helper_fetch_url_content(filename)
            if full_text.startswith("Error"): return full_text 
        except Exception as e: return f"Web Error: {str(e)}"
    else:
        file_path = sandbox_dir / filename
        if not file_path.exists(): return f"Error: File '{filename}' not found."
        try:
            if filename.lower().endswith(".pdf"):
                doc = fitz.open(file_path)
                for i, page in enumerate(doc):
                    text = page.get_text()
                    if text: full_text += text + "\n"
                doc.close()
            else:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    full_text = f.read()
        except Exception as e: return f"Disk Error: {str(e)}"

    if not full_text.strip(): return "Error: Extracted text is empty."

    pretty_log("Text Processing", f"Splitting {len(full_text)} chars", icon=Icons.MEM_SPLIT)
    chunks = recursive_split_text(full_text, chunk_size=1000, chunk_overlap=100)
    if not chunks: return "Error: No chunks created."

    pretty_log("Vector Embedding", f"Processing {len(chunks)} fragments", icon=Icons.MEM_EMBED)
    try:
        def batch_ingest(chunk_list, source_name):
            # Reduced batch size for smoother upstream LLM processing
            batch_size = 25 
            for i in range(0, len(chunk_list), batch_size):
                batch = chunk_list[i : i + batch_size]
                ids = [hashlib.md5(f"{source_name}_{i+j}_{chunk[:20]}".encode()).hexdigest() for j, chunk in enumerate(batch)]
                metadatas = [{"source": source_name, "type": "document", "chunk_index": i+j, "timestamp": get_utc_timestamp()} for j in range(len(batch))]
                memory_system.collection.upsert(documents=batch, metadatas=metadatas, ids=ids)
                
                # Progress logging every 2 batches
                if i % 50 == 0:
                    pretty_log("Ingestion Progress", f"Processed {min(i+batch_size, len(chunk_list))}/{len(chunk_list)} fragments", icon=Icons.MEM_EMBED)
        
        await asyncio.to_thread(batch_ingest, chunks, filename)
        preview = full_text[:300].replace("\n", " ") + "..."
    except Exception as e: return f"Embedding Error: {e}"

    try: await asyncio.to_thread(memory_system._update_library_index, filename, "add")
    except: pass 

    return f"SUCCESS: Ingested '{filename}'."

async def tool_recall(query: str, memory_system):
    pretty_log("Conceptual Recall", query, icon=Icons.MEM_READ)
    if not memory_system: return "Error: Memory system is disabled."
    try:
        results = await asyncio.to_thread(memory_system.search_advanced, query, limit=5)
    except: return "Error: Memory retrieval failed."

    valid_chunks = []
    for res in results:
        score = res.get('score', 1.0)
        source = res.get('metadata', {}).get('source', 'Unknown')
        text = res.get('text', '')
        
        if score < 0.8: relevance = "HIGH"
        elif score < 1.1: relevance = "MEDIUM"
        else: relevance = "LOW"
        
        pretty_log("Memory Match", f"[{relevance}] {score:.3f} | {source}", icon=Icons.MEM_MATCH)

        if score < 1.3:
            valid_chunks.append(f"SOURCE: {source}\nCONTENT: {text}")

    if valid_chunks:
        return f"SYSTEM: Found {len(valid_chunks)} memories.\n\n" + "\n\n".join(valid_chunks)
    else:
        return "SYSTEM OBSERVATION: Zero relevant documents found."

async def tool_unified_forget(target: str, sandbox_dir: Path, memory_system):
    pretty_log("Memory Purge", target, icon=Icons.MEM_WIPE)
    if not memory_system: return "Report: Memory disabled."
    report = []
    try:
        disk_match = next((f for f in os.listdir(sandbox_dir) if target.lower() in f.lower()), None)
        if disk_match:
            (sandbox_dir / disk_match).unlink()
            report.append(f"✅ Disk: Deleted '{disk_match}'")
    except: pass

    try:
        data = await asyncio.to_thread(memory_system.collection.get, include=["metadatas"])
        all_sources = set()
        if data and "metadatas" in data:
            for meta in data["metadatas"]:
                if meta and "source" in meta: all_sources.add(meta["source"])
        
        db_match = next((s for s in all_sources if target.lower() in s.lower()), None)
        if db_match:
            await asyncio.to_thread(memory_system.delete_document_by_name, db_match)
            report.append(f"✅ Memory: Wiped document '{db_match}'.")

        results = memory_system.collection.query(query_texts=[target], n_results=5)
        if results['ids']:
            for i, dist in enumerate(results['distances'][0]):
                if dist < 0.6 or target.lower() in results['documents'][0][i].lower():
                    memory_system.collection.delete(ids=[results['ids'][0][i]])
                    report.append(f"✅ Sweep: Forgot fact.")
    except Exception as e: report.append(f"⚠️ Memory Error: {e}")

    return "\n".join(report) if report else f"No match for '{target}'."

async def tool_scratchpad(action: str, scratchpad: Scratchpad, key: str = None, value: str = None):
    icon = Icons.MEM_SAVE if action == "set" else Icons.MEM_READ
    log_title = f"Variable {action.upper()}"
    log_content = f"{key} = {value}" if value else key
    pretty_log(log_title, log_content, icon=icon)
    if action == "set":
        return scratchpad.set(key, value)
    elif action == "get":
        val = scratchpad.get(key)
        return f"{key} = {val}" if val else f"Error: '{key}' not found."
    elif action == "list":
        return scratchpad.list_all()
    return "Error: Unknown action"

async def tool_update_profile(category: str, key: str, value: str, profile_memory, memory_system):
    pretty_log("Profile Update", f"{category}.{key}={value}", icon=Icons.USER_ID)
    if not profile_memory: return "Error: Profile memory not loaded."
    msg = profile_memory.update(category, key, value)
    if memory_system:
        try: await asyncio.to_thread(memory_system.smart_update, f"User {key} is {value}", "identity")
        except: pass
    return f"SUCCESS: Profile updated."

async def tool_knowledge_base(action: str, sandbox_dir: Path, memory_system, scratchpad: Scratchpad = None, profile_memory = None, content: str = None, source: str = None, key: str = None, value: str = None, category: str = None):

    target = content or source

    if action == "insert_fact":

        return await tool_remember(target, memory_system)

    elif action == "ingest_document":

        return await tool_gain_knowledge(target, sandbox_dir, memory_system)

    elif action == "forget":

        return await tool_unified_forget(target, sandbox_dir, memory_system)

    elif action == "list_docs":

        library = memory_system.get_library() or []

        return f"LIBRARY CONTENTS ({len(library)} files):\n" + "\n".join([f"- {doc}" for doc in library]) if library else "No docs."

    elif action == "reset_all":

        all_ids = memory_system.collection.get()['ids']

        if all_ids:

            for i in range(0, len(all_ids), 500): memory_system.collection.delete(ids=all_ids[i:i+500])

        memory_system.library_file.write_text("[]")

        return "Success: Wiped clean."

    elif action == "scratchpad":

        return await tool_scratchpad(content, scratchpad, key, value)

    elif action == "update_profile":
        
        cat = category or content
        return await tool_update_profile(cat, key, value, profile_memory, memory_system) 

    return f"Error: Unknown action '{action}'"
