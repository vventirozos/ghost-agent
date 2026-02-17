import re
import tokenize
import io
import ast
from typing import Optional, Tuple, List

def extract_code_from_markdown(text: str) -> str:
    """
    Extracts code from markdown blocks if present.
    """
    # Relaxed pattern: Allow missing newline after language identifier
    # Matches: ```python code... ``` or ```python\ncode...```
    # Relaxed pattern: Matches ``` then optional language, then any newline/space, then code, then ```
    # handle optional spaces before language, and lenient newline check
    code_block_pattern = re.compile(r'```[ \t]*(?:[a-zA-Z]+)?(?:[ \t]*\n|[ \t]+)(.*?)```', re.DOTALL)
    match = code_block_pattern.search(text)
    if match:
        return match.group(1).strip()
    
    # Fallback: maybe just ``` without language or closing ```
    if text.strip().startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
        
    return text

def _repair_line(line: str) -> str:
    """
    Applies aggressive regex fixes to a single line based on common hallucinations.
    """
    # 0. Strip unexpected trailing backslash (causes: SyntaxError: unexpected character after line continuation)
    match = re.search(r'(\\+)\s*$', line)
    if match:
        num_slashes = len(match.group(1))
        if num_slashes % 2 != 0:
            # Strip the dangling backslash and any invisible trailing whitespace
            # We preserve (N-1) backslashes (which is an even number, so valid escaped backslashes)
            line = line[:match.start()] + ('\\' * (num_slashes - 1))

    # Fix: Trailing backslash or escaped quote at EOL (keep quote if it was escaped)
    # Hallucination: print("...\" ) -> print("...")
    # NOTE: We use negative lookbehind (?<!\\) to ensure we don't match \\" which is valid escaped backslash + quote
    line = re.sub(r'(?<!\\)\\([\'"])\s*$', r'\1', line).rstrip()
    line = re.sub(r'(?<!\\)\\([\'"]?)\s*\)\s*$', r'\1)', line)
     
    # Fix: hallucinated escape sequences in f-strings or prints
    # f\" -> f" , print(\" -> print("
    line = re.sub(r'([fbr\(,{])\\([\'"])', r'\1\2', line)
    # \") -> ")
    line = re.sub(r'(?<!\\)\\([\'"])([\),])', r'\1\2', line)
    
    # Fix: Trailing backticks at EOL (common hallucination: print("hi")`)
    line = line.rstrip('`')
    
    # Fix: Unterminated string literals (simple heuristic)
    # Check for lines starting with simple assignment or print, having an open quote but no close
    # This is conservative to avoid breaking multi-line strings
    strip_line = line.strip()
    if (strip_line.startswith("print(") or " = " in strip_line) and not strip_line.endswith(":"):
        # Count quotes
        dq_count = line.count('"') - line.count('\\"')
        sq_count = line.count("'") - line.count("\\'")
        
        # If odd number of double quotes, and line ends with text (not a quote), append one
        if dq_count % 2 != 0 and not line.strip().endswith('"') and not line.strip().endswith('",') and not line.strip().endswith('")'):
             # Attempt to close it. Check if we need a paren too.
             if "(" in line and ")" not in line:
                 line += '")'
             else:
                 line += '"'
                 
        # If odd number of single quotes
        elif sq_count % 2 != 0 and not line.strip().endswith("'") and not line.strip().endswith("',") and not line.strip().endswith("')"):
             if "(" in line and ")" not in line:
                 line += "')"
             else:
                 line += "'"
    
    return line

def fix_python_syntax(code: str) -> str:
    """
    Attempts to fix common Python syntax errors using a combination of regex and tokenization checks.
    """
    # 0. Brute-force cleanup
    code = re.sub(r'(\?[\w,]{1,3}){3,}', '', code) # Stuttering
    code = re.sub(r'(\?){3,}$', '', code) # Trailing ? sequence (stuttering)
    code = code.rstrip('`') # Trailing backticks at end of file
    
    # --- MASHED NEWLINE HEURISTIC ---
    if "\\n" in code:
        try:
            ast.parse(code)
        except SyntaxError:
            # Speculatively unescape the string
            speculative_code = code.replace("\\n", "\n").replace("\\t", "\t")
            speculative_code = speculative_code.replace('\\"', '"').replace("\\'", "'")
            try:
                ast.parse(speculative_code)
                code = speculative_code  # Unescaping fixed the syntax!
            except SyntaxError:
                # If it still fails but the original was mashed on 1 line, keep the unescaped version.
                # This allows the line-by-line repair logic below to actually work.
                if code.count('\n') == 0:
                    code = speculative_code

    # 1. Initial Parse Check
    try:
        ast.parse(code)
        return code
    except SyntaxError:
        pass

    # 2. Line-by-line Repair
    lines = code.splitlines()
    fixed_lines = [_repair_line(line) for line in lines]
    code = "\n".join(fixed_lines)
    
    try:
        ast.parse(code)
        return code
    except SyntaxError:
        pass
        
    # 3. Bracket Balancing (for truncated code)
    # This is a heuristic to close open brackets/parentheses
    stack = []
    # robust tokenization to find open brackets
    try:
        # We iterate manually to catch TokenError gracefully while keeping partial results
        token_gen = tokenize.tokenize(io.BytesIO(code.encode('utf-8')).readline)
        for token in token_gen:
            if token.type == tokenize.OP:
                if token.string in '([{':
                    stack.append(token.string)
                elif token.string in ')]}':
                    if stack:
                        # minimal check for matching
                        curr = stack[-1]
                        if (curr == '(' and token.string == ')') or \
                           (curr == '[' and token.string == ']') or \
                           (curr == '{' and token.string == '}'):
                            stack.pop()
    except tokenize.TokenError:
        # Token error means likely unterminated string or similar
        pass
        
    # Close any remaining brackets
    mapping = {'(': ')', '[': ']', '{': '}'}
    closer = "".join([mapping.get(x, '') for x in reversed(stack)])
    if closer:
        code += "\n" + closer
        
    return code

def sanitize_code(content: str, filename: str) -> Tuple[str, Optional[str]]:
    """
    Sanitizes code content.
    Returns: (sanitized_code, error_message)
    """
    ext = str(filename).split('.')[-1].lower()
    
    # 1. Extract from Markdown
    content = extract_code_from_markdown(content)
    
    # 1.5 Scrub Control Characters (Prevent ^H / Backspace injection)
    # We allow: \n (10), \r (13), \t (9) and everything >= 32 (Space)
    content = "".join(ch for ch in content if ord(ch) >= 32 or ch in "\n\r\t")
    
    # 2. Language specific fixes
    if ext == "py":
        content = fix_python_syntax(content)
        # Final Verification
        try:
            ast.parse(content)
        except SyntaxError as e:
            # We return the content anyway, but with an error message
            # The execution tool might decide to run it anyway or report the error.
            # But the requirement says "return a helpful error".
            return content, f"SyntaxError: {e}"
            
    return content, None
