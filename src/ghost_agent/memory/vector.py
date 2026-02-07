import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

import chromadb
from chromadb.config import Settings

from ..utils.logging import Icons, pretty_log
from ..utils.helpers import get_utc_timestamp

from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

logger = logging.getLogger("GhostAgent")

class GhostEmbeddingFunction(EmbeddingFunction):
    """
    Custom robust embedding function that uses the upstream LLM.
    Handles proxy bypass and retries.
    """
    def __init__(self, upstream_url: str):
        import httpx
        self.url = f"{upstream_url}/v1/embeddings"
        # Explicitly disable proxy for local LLM and disable http2
        self.client = httpx.Client(timeout=60.0, proxy=None, http2=False)

    def __call__(self, input: Documents) -> Embeddings:
        # ChromaDB expects a List of Embeddings
        import time
        for attempt in range(3):
            try:
                resp = self.client.post(self.url, json={"input": input, "model": "default"})
                resp.raise_for_status()
                data = resp.json()
                return [item["embedding"] for item in data["data"]]
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    logger.error(f"Embedding failed after 3 attempts: {e}")
                    raise

class VectorMemory:
    def __init__(self, memory_dir: Path, upstream_url: str):
        """
        Robust Initialization with Explicit Settings.
        """
        self.chroma_dir = memory_dir
        if not self.chroma_dir.exists():
            self.chroma_dir.mkdir(parents=True, exist_ok=True)

        self.library_file = self.chroma_dir / "library_index.json"
        if not self.library_file.exists():
            self.library_file.write_text("[]")

        try:
            # Use our custom robust embedding function
            self.embedding_fn = GhostEmbeddingFunction(upstream_url)
        except Exception as e:
            logger.error(f"Error initializing embedding function: {e}")
            sys.exit(1)

        try:
            self.client = chromadb.PersistentClient(
                path=str(self.chroma_dir),
                settings=Settings(
                    allow_reset=True,
                    anonymized_telemetry=False
                )
            )
            
            # Use a collection name that reflects the embedding provider to avoid conflicts
            collection_name = "agent_memory_v2" # Renamed to avoid conflict with 'sentence_transformer' version
            
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_fn
            )
            
            pretty_log(f"Memory System: Initialized [{collection_name}] ({self.collection.count()} items)", icon="🧠")
            
        except Exception as e:
            if "already exists" in str(e) or "Embedding function conflict" in str(e):
                pretty_log("Memory Conflict", "Embedding provider mismatch. Resetting collection for new provider...", level="WARNING", icon="⚠️")
                # Fallback: if 'v2' also conflicts (unlikely), we'd need to reset. 
                # For now, renaming to v2 is the safest non-destructive path.
                sys.exit(1)
            logger.error(f"CRITICAL DB ERROR: {e}")
            self.collection = None

    def search_advanced(self, query: str, limit: int = 5):
        results = self.collection.query(
            query_texts=[query],
            n_results=limit
        )
        
        parsed_results = []
        if results['ids']:
            for i in range(len(results['ids'][0])):
                parsed_results.append({
                    "id": results['ids'][0][i],
                    "text": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i],
                    "score": results['distances'][0][i]
                })
        
        return parsed_results

    def _update_library_index(self, filename: str, action: str):
        try:
            data = json.loads(self.library_file.read_text())
            if action == "add" and filename not in data:
                data.append(filename)
            elif action == "remove" and filename in data:
                data.remove(filename)
            self.library_file.write_text(json.dumps(data))
        except Exception as e:
            logger.error(f"Library index error: {e}")

    def get_library(self):
        if not self.library_file.exists():
            return []
        try:
            data = json.loads(self.library_file.read_text())
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []
    
    def add(self, text: str, meta: dict = None):
        if len(text) < 5: return
        mem_id = hashlib.md5(text.encode("utf-8")).hexdigest()

        existing = self.collection.get(ids=[mem_id])
        if existing and existing['ids']:
            return

        metadata = meta or {"timestamp": get_utc_timestamp(), "type": "auto"}
        self.collection.add(documents=[text], metadatas=[metadata], ids=[mem_id])
        pretty_log("Memory Stored", text[:100], icon=Icons.MEM_SAVE)

    def smart_update(self, text: str, type_label: str = "auto"):
        try:
            results = self.collection.query(query_texts=[text], n_results=1)
            if results['ids'] and results['ids'][0]:
                dist = results['distances'][0][0]
                existing_id = results['ids'][0][0]

                if dist < 0.5:
                    self.collection.delete(ids=[existing_id])
                    pretty_log("Smart Memory", "Overwriting Old Memory", icon="♻️")

            self.add(text, {"timestamp": get_utc_timestamp(), "type": type_label})
        except Exception as e:
            logger.error(f"Smart Update Error: {e}")

    def ingest_document(self, filename: str, chunks: List[str]):
        try:
            ids = [hashlib.md5(f"{filename}_{i}_{chunk[:20]}".encode()).hexdigest() for i, chunk in enumerate(chunks)]
            metadatas = [{"timestamp": get_utc_timestamp(), "type": "document", "source": filename} for _ in range(len(chunks))]

            batch_size = 20
            for i in range(0, len(chunks), batch_size):
                self.collection.upsert(
                    documents=chunks[i:i + batch_size],
                    metadatas=metadatas[i:i + batch_size],
                    ids=ids[i:i + batch_size]
                )
                if i % 10 == 0:
                    pretty_log(f"Ingesting {filename}", f"Chunk {i+1}/{len(chunks)}", icon="📚")

            self._update_library_index(filename, "add")
            return True, f"Successfully ingested {len(chunks)} chunks from {filename}."
        except Exception as e:
            logger.error(f"Ingest failed: {e}")
            return False, str(e)

    def search(self, query: str, inject_identity: bool = True):
            try:
                search_queries = [query]
                if inject_identity:
                    search_queries.insert(0, "User's profile. User's name. User preferences.")

                results = self.collection.query(
                    query_texts=search_queries,
                    n_results=10,
                )

                candidates = []
                seen_docs = set()

                def process_batch(batch_idx, is_identity_batch):
                    if not results['documents'] or len(results['documents']) <= batch_idx:
                        return

                    for doc, meta, dist in zip(
                        results['documents'][batch_idx],
                        results['metadatas'][batch_idx],
                        results['distances'][batch_idx]
                    ):
                        if doc in seen_docs: continue

                        m_type = meta.get('type', 'auto')
                        doc_lower = doc.lower()
                        timestamp = meta.get('timestamp', '0000-00-00')

                        is_summary = m_type == "document_summary"

                        is_name_memory = (
                            "name is" in doc_lower or
                            "call me" in doc_lower or
                            "user's" in doc_lower or
                            "user is" in doc_lower
                        )

                        if is_name_memory:
                            threshold = 1.2 
                        elif is_summary:
                            threshold = 0.85
                        elif is_identity_batch:
                            threshold = 0.9 if m_type == 'manual' else 0.75
                        else:
                            threshold = 0.85 if m_type == 'manual' else 0.70

                        if dist < threshold or is_name_memory or is_summary:
                            priority_score = 1

                            if is_name_memory: priority_score = -20
                            elif is_summary: priority_score = -15
                            elif is_identity_batch: priority_score = -10
                            elif m_type == 'manual': priority_score = 0
                            elif m_type == 'document': priority_score = 2

                            candidates.append({
                                "doc": doc,
                                "meta": meta,
                                "dist": dist,
                                "type": m_type,
                                "p_score": priority_score,
                                "timestamp": timestamp
                            })
                            seen_docs.add(doc)

                if inject_identity:
                    process_batch(0, is_identity_batch=True)
                    process_batch(1, is_identity_batch=False)
                else:
                    process_batch(0, is_identity_batch=False)

                candidates.sort(key=lambda x: (x['p_score'], x['timestamp']), reverse=False)

                final_selection = candidates[:6]
                if not final_selection: return ""

                output = []
                for item in final_selection:
                    ts = item['meta'].get('timestamp', '?')
                    m_type = item['meta'].get('type', 'auto').upper()
                    doc_text = item['doc']

                    prefix = ""
                    if item['p_score'] <= -15: prefix = "**[MASTER SUMMARY]** "
                    elif item['p_score'] <= -10: prefix = "**[IDENTITY]** "
                    elif item['p_score'] == 0: prefix = "**[USER PRIORITY]** "
                    elif item['p_score'] == 2: prefix = "**[DOCUMENT SOURCE]** "

                    output.append(f"[{ts}] ({m_type}) {prefix}{doc_text}")

                return "\n---\n".join(output)

            except Exception as e:
                logger.error(f"Search failed: {e}")
                return ""

    def delete_document_by_name(self, filename: str):
        self.collection.delete(where={"source": filename})
        self._update_library_index(filename, "remove")
        return True, "Deleted"

    def delete_by_query(self, query: str):
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=1,
                where={"type": {"$ne": "document"}}
            )
            if not results['ids'] or not results['ids'][0]:
                return False, "Memory not found."

            dist = results['distances'][0][0]
            doc_text = results['documents'][0][0]
            mem_id = results['ids'][0][0]

            if dist > 0.5:
                return False, f"Best match was '{doc_text}' but score ({dist:.2f}) was too low."

            self.collection.delete(ids=[mem_id])
            pretty_log("Memory Deleted", doc_text, icon="🗑️")
            return True, f"Successfully forgot: [[{doc_text}]]"
        except Exception as e:
            return False, f"Error: {e}"