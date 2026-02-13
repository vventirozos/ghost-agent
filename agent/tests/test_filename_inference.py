import asyncio
import os
from pathlib import Path
# Mock the tool logic to avoid full environment setup
import urllib.parse

def infer_filename(url):
    parsed = urllib.parse.urlparse(str(url))
    target_path = os.path.basename(parsed.path)
    if not target_path: target_path = "downloaded_file"
    return target_path

def test_inference():
    # Test 1: Standard file
    url1 = "https://www.gutenberg.org/cache/epub/1513/pg1513.txt"
    fname1 = infer_filename(url1)
    print(f"URL: {url1} -> Filename: {fname1} {'[PASS]' if fname1 == 'pg1513.txt' else '[FAIL]'}")
    
    # Test 2: No filename
    url2 = "https://example.com/"
    fname2 = infer_filename(url2)
    print(f"URL: {url2} -> Filename: {fname2} {'[PASS]' if fname2 == 'downloaded_file' else '[FAIL]'}")

    # Test 3: Query params
    url3 = "https://example.com/foo.pdf?bar=baz"
    fname3 = infer_filename(url3)
    print(f"URL: {url3} -> Filename: {fname3} {'[PASS]' if fname3 == 'foo.pdf' else '[FAIL]'}")

if __name__ == "__main__":
    test_inference()
