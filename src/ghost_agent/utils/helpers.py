import datetime
import os
import httpx
from typing import List

async def helper_fetch_url_content(url: str) -> str:
    # 1. Setup Tor Proxy
    proxy_url = os.getenv("TOR_PROXY", "socks5://127.0.0.1:9050")
    if proxy_url and proxy_url.startswith("socks5://"): 
        proxy_url = proxy_url.replace("socks5://", "socks5h://")

    try:
        # 2. Inject Proxy into Client
        async with httpx.AsyncClient(proxy=proxy_url, timeout=20.0, follow_redirects=True) as client:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            resp = await client.get(url, headers=headers)
            
            if resp.status_code != 200:
                return f"Error: Received status {resp.status_code} from {url}"
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, 'html.parser')
            for script in soup(["script", "style", "nav", "footer", "iframe", "svg"]):
                script.decompose()
            
            text = soup.get_text(separator=' ', strip=True)
            text = " ".join(text.split())
            return text if text else "Error: No text content extracted from page."
            
    except Exception as e:
        return f"Error reading {url}: {str(e)}"

def get_utc_timestamp():
    """Returns strict ISO8601 UTC timestamp for Go/iOS clients."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def recursive_split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 70) -> List[str]:
    if not text: return []
    if len(text) <= chunk_size: return [text]
    
    separators = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""]
    final_chunks = []
    stack = [text]
    
    while stack:
        current_text = stack.pop()
        
        if len(current_text) <= chunk_size:
            final_chunks.append(current_text)
            continue
            
        found_sep = ""
        for sep in separators:
            if sep in current_text:
                found_sep = sep
                break
        
        if not found_sep:
            for i in range(0, len(current_text), chunk_size - chunk_overlap):
                final_chunks.append(current_text[i:i+chunk_size])
            continue
            
        parts = current_text.split(found_sep)
        buffer = ""
        temp_chunks = []
        
        for p in parts:
            fragment = p + found_sep if found_sep.strip() else p
            if len(buffer) + len(fragment) <= chunk_size:
                buffer += fragment
            else:
                if buffer:
                    temp_chunks.append(buffer.strip())
                buffer = fragment
        
        if buffer:
            temp_chunks.append(buffer.strip())

        for chunk in reversed(temp_chunks):
            if len(chunk) > chunk_size:
                if found_sep == "":
                    for i in range(0, len(chunk), chunk_size - chunk_overlap):
                        final_chunks.append(chunk[i:i+chunk_size])
                else:
                    stack.append(chunk) 
            else:
                final_chunks.append(chunk)

    return final_chunks