import ast

# The code snippet reported in the logs
# The mashed code from logs (Complex case)
bad_code = r"""
import re
from pathlib import Path
text = Path('romeo.txt').read_text()
# Complex regex with groups and symbols, mashed with print
love_count = len(re.findall(r\b(love|hate) print(f"Love appears {love_count} times." )))
"""

def test_parse():
    print("Original Code Parse Try:")
    try:
        ast.parse(bad_code)
        print("Parsed successfully (Unexpected)")
    except SyntaxError as e:
        print(f"SyntaxError detected: {e}")

    print("\nSanitizing Code...")
    from ghost_agent.utils.sanitizer import sanitize_code
    sanitized, error = sanitize_code(bad_code, "test.py")
    
    print(f"Sanitized Code:\n---\n{sanitized}\n---")
    
    print("\nParsed Sanitized Code:")
    try:
        ast.parse(sanitized)
        print("SUCCESS: Sanitized code parsed successfully!")
    except SyntaxError as e:
        print(f"FAILURE: Sanitized code still has SyntaxError: {e}")

if __name__ == "__main__":
    test_parse()
