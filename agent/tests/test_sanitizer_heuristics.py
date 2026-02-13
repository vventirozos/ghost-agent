import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from ghost_agent.utils.sanitizer import sanitize_code
import ast

def test_heuristics():
    # The mashed code from logs
    bad_code = r"""
import re
from pathlib import Path
text = Path('romeo.txt').read_text()
love_count = len(re.findall(r\blove print(f"Love appears {love_count} times." )))
"""
    print(f"Bad Code:\n{bad_code}")
    
    sanitized, err = sanitize_code(bad_code, "test.py")
    print(f"\nSanitized:\n{sanitized}")
    
    try:
        ast.parse(sanitized)
        print("\nPASSED: Parses successfully.")
    except SyntaxError as e:
        print(f"\nFAILED: {e}")

if __name__ == "__main__":
    test_heuristics()
