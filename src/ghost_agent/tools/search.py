import asyncio
import importlib.util
import json
import os
from typing import List, Dict, Any, Callable
from ..utils.logging import Icons, pretty_log
from ..utils.helpers import helper_fetch_url_content

def truncate_query(query: str, limit: int = 35) -> str:
    return (query[:limit] + "..") if len(query) > limit else query

async def tool_search_ddgs(query: str, tor_proxy: str):
    # Log with TOR status and truncated query
    pretty_log("DDGS Search", query, icon=Icons.TOOL_SEARCH)
    
    def format_search_results(results: List[Dict]) -> str:
        if not results: return "No results found."
        formatted = []
        for i, res in enumerate(results, 1):
            title = res.get('title', 'No Title')
            body = res.get('body', res.get('content', 'No content'))
            link = res.get('href', res.get('url', '#'))
            formatted.append(f"### {i}. {title}\n{body}\n[Source: {link}]")
        return "\n\n".join(formatted)
    
    if not importlib.util.find_spec("ddgs"):
        return "Search unavailable (Library 'ddgs' not installed)."

    from ddgs import DDGS
    for attempt in range(3):
        try:
            def run():
                with DDGS(proxy=tor_proxy, timeout=15) as ddgs:
                    return list(ddgs.text(query, max_results=3))
            raw_results = await asyncio.to_thread(run)
            clean_output = format_search_results(raw_results)
            return clean_output
        except Exception:
            if attempt < 2:
                await asyncio.sleep(1)

    return "Error: Search failed after 3 retries."

async def tool_search(query: str, anonymous: bool, tor_proxy: str):
    # Tavily support removed. Always using DDGS.
    return await tool_search_ddgs(query, tor_proxy)

async def tool_deep_research(query: str, anonymous: bool, tor_proxy: str):
    pretty_log("Deep Research", query, icon=Icons.TOOL_DEEP)
    
    urls = []
    try:
        if importlib.util.find_spec("ddgs"):
            from ddgs import DDGS
            with DDGS(proxy=tor_proxy, timeout=15) as ddgs:
                results = list(ddgs.text(query, max_results=2))
                urls = [r.get('href') for r in results]
    except Exception:
        return f"Error: Search failed."

    if not urls: return "Error: No search results found."

    sem = asyncio.Semaphore(2) 
    async def process_url(url):
        async with sem:
            # Shorten URL for log
            short_url = (url[:35] + "..") if len(url) > 35 else url
            pretty_log("Parsing Data", url, icon=Icons.TOOL_FILE_R)
            text = await helper_fetch_url_content(url)
            # Reduce preview to 2000 chars to keep context lean
            preview = text[:2000] 
            return f"### SOURCE: {url}\n{preview}\n[...truncated...]\n"

    tasks = [process_url(u) for u in urls]
    page_contents = await asyncio.gather(*tasks)
    full_report = "\n\n".join(page_contents)
    return f"--- DEEP RESEARCH RESULT ---\n{full_report}\n\nSYSTEM INSTRUCTION: Analyze the text above."

async def tool_fact_check(statement: str, http_client, tool_definitions, deep_research_callable: Callable):
    pretty_log("Fact Check", statement, icon=Icons.STOP)
    
    allowed_names = ["deep_research"]
    restricted_tools = [t for t in tool_definitions if t["function"]["name"] in allowed_names]
    
    messages = [
        {"role": "system", "content": "### ROLE: DEEP FORENSIC VERIFIER\nVerify this claim with deep_research."},
        {"role": "user", "content": statement}
    ]

    payload = {
        "model": "ghost-agent",
        "messages": messages,
        "tools": restricted_tools,
        "tool_choice": "required"
    }

    try:
        resp = await http_client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls:
            return "Error: Sub-agent failed."

        call = tool_calls[0]
        func_name = call["function"]["name"]
        func_args = json.loads(call["function"]["arguments"])
        
        if func_name == "deep_research":
            research_result = await deep_research_callable(**func_args)
            
            messages.append(msg)
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": func_name,
                "content": str(research_result)
            })
            
            payload["tool_choice"] = "none"
            payload["messages"] = messages
            
            final_resp = await http_client.post("/v1/chat/completions", json=payload)
            final_resp.raise_for_status()
            content = final_resp.json()["choices"][0]["message"]["content"]
            
            return f"Verification Complete. Present these findings:\n\n{content}"

        return f"Error: Unauthorized tool"

    except Exception as e:
        return f"Fact check failure: {e}"