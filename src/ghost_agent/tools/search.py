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
        if not results: return "ERROR: DuckDuckGo returned ZERO results. This usually means the query was too specific or the search engine is blocking the request (CAPTCHA/Tor). TRY A BROADER QUERY."
        formatted = []
        for i, res in enumerate(results, 1):
            title = res.get('title', 'No Title')
            body = res.get('body', res.get('content', 'No content'))
            link = res.get('href', res.get('url', '#'))
            formatted.append(f"### {i}. {title}\n{body}\n[Source: {link}]")
        return "\n\n".join(formatted)

    if not importlib.util.find_spec("ddgs"):
        return "CRITICAL ERROR: 'ddgs' library is missing. Search is impossible."

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
                results = list(ddgs.text(query, max_results=5))
                # FILTER: Skip known junk sites that often appear on Tor blocks
                junk = ["forums.att.com", "reddit.com", "quora.com", "facebook.com", "twitter.com"]
                for r in results:
                    url = r.get('href', '').lower()
                    if not any(j in url for j in junk):
                        urls.append(r.get('href'))
                # If we filtered everything, just take the first result as a fallback
                if not urls and results:
                    urls = [results[0].get('href')]
                # Keep only top 2 high-quality links
                urls = urls[:2]
    except Exception:
        return f"CRITICAL ERROR: Deep Research search phase failed."

    if not urls: return "ERROR: No search results found. The internet might be blocking your request. Try a different query."

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