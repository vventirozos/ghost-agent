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
    code_block_pattern = re.compile(r'```(?:[a-zA-Z]*)(?:\n|\s)(.*?)```', re.DOTALL)
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
    # Only strip if we have an ODD number of trailing backslashes.
    # e.g. "abc\" -> Strip (1 slash)
    # e.g. "abc\\" -> Keep (2 slashes = escaped slash)
    # e.g. "abc\\\" -> Strip (3 slashes)
    match = re.search(r'(\\+)\s*$', line)
    if match:
        num_slashes = len(match.group(1))
        if num_slashes % 2 != 0:
            # Remove the last char (the trailing backslash)
            # Remove the last char (the trailing backslash)
            # handle case where whitespace follows the backslash
            # match.start(1) is the start of the backslashes
            # match.end(1) is the end of the backslashes
            # we want to keep line[:match.end(1)-1] (strip one slash)
            # but we also want to strip trailing whitespace? 
            # If line is "abc \ ", we want "abc ".
            # If line is "abc \", we want "abc".
            # The original code did: line.rstrip()[:-1].rstrip()
            # If "abc \ ", rstrip->"abc \", [:-1]->"abc ", rstrip->"abc". Correct.
            # But just to be safe and explicit:
            end_of_slashes = match.end(1)
            line = line[:end_of_slashes-1] + line[match.end(0):]
            line = line.rstrip()
    
    # Fix: Backslash followed by comment (SyntaxError: unexpected character after line continuation character)
    # Pattern: \ # comment  -> Strip the backslash
    line = re.sub(r'\\\s*#.*$', '', line).rstrip()

    # Fix: Trailing backslash or escaped quote at EOL (keep quote if it was escaped)
    # Hallucination: print("...\" ) -> print("...")
    # NOTE: We removed the optional '?' from [\'"] so we don't clobber simple trailing backslashes
    # which are already handled by the odd/even check above.
    line = re.sub(r'\\([\'"])\s*$', r'\1', line).rstrip()
    line = re.sub(r'\\([\'"]?)\s*\)\s*$', r'\1)', line)
     
    # Fix: hallucinated escape sequences in f-strings or prints
    # f\" -> f" , print(\" -> print("
    line = re.sub(r'([fbr\(,{])\\([\'"])', r'\1\2', line)
    # \") -> ")
    line = re.sub(r'\\([\'"])([\),])', r'\1\2', line)
    
    # Fix: Trailing backticks at EOL (common hallucination: print("hi")`)
    line = line.rstrip('`')
    
    return line

def _repair_hallucinated_line_continuation(code: str) -> str:
    """
    Uses tokenization to find and repair invalid backslashes that are NOT inside strings.
    A backslash at the end of a line is valid if followed by a newline.
    If it's followed by a space or other character (hallucination), it is a SyntaxError.
    """
    try:
        # We need to process the code to find invalid tokens
        # tokenize.tokenize will produce ERRORTOKEN for invalid backslashes
        # We collect the lines that need repair
        lines = code.splitlines()
        modified_lines = list(lines) # Copy for modification
        
        # We iterate manually to catch TokenError gracefully while keeping partial results
        try:
             # readline for tokenize
            token_gen = tokenize.tokenize(io.BytesIO(code.encode('utf-8')).readline)
            for token in token_gen:
                # ERRORTOKEN with string='\' is what we are looking for
                # But typically tokenize might just fail or produce ERRORTOKEN for other things.
                # Only explicit backslash errors are what we target.
                if token.type == tokenize.ERRORTOKEN and token.string == '\\':
                    # This token is invalid.
                    # Verify if it is at the end of the line (or close to it) in the original code
                    lineno = token.start[0] - 1 # 0-indexed
                    if lineno < len(lines):
                        # Get the line
                        line = lines[lineno]
                        # Find the backslash position
                        col_offset = token.start[1]
                        
                        # Verify it really is a backslash there
                        if col_offset < len(line) and line[col_offset] == '\\':
                            # We strip everything from the backslash onwards?
                            # Or just the backslash?
                            # If it's "x = 1 \2", stripping backslash gives "x = 1 2" which is still invalid
                            # but better than "unexpected character".
                            # Actually, usually the hallucination is " \ explanation"
                            # So stripping from backslash to end of line is safer.
                            # BUT we must refer to where the check_tokenize showed us.
                            # It showed ERRORTOKEN for '\' followed by NUMBER or NAME.
                            
                            # Strategy: Strip the backslash, don't truncate.
                            # Truncating destroys valid code (e.g. x = r\blove -> x = r).
                            # Stripping gives x = rblove, which is better.
                            modified_lines[lineno] = line[:col_offset] + line[col_offset+1:]
                            
        except (tokenize.TokenError, IndentationError):
             pass
             
        return "\n".join(modified_lines)

    except Exception:
        # Fallback: if tokenization completely fails (e.g. encoding issues), return original
        # BUT if we made partial progress (modified_lines), maybe we should return that?
        # The variables 'modified_lines' might be unbound if Exception happens before assignment.
        # But here 'modified_lines' is assigned early.
        try:
             return "\n".join(modified_lines)
        except UnboundLocalError:
             return code

def _convert_multiline_strings(code: str) -> str:
    """
    Detects single/double quoted strings that span multiple lines (SyntaxError)
    and converts them to triple quoted strings.
    """
    # We need to tokenize to find strings safely, but tokenize fails on these errors.
    # So we must use a custom state machine similar to _close_open_strings but focused on *fixing* the start/end quotes.
    
    # State machine:
    # 0: normal
    # 1: inside '
    # 2: inside "
    # 3: inside '''
    # 4: inside """
    
    state = 0
    escaped = False
    i = 0
    length = len(code)
    
    # buffers for string content
    current_string_start = -1
    contains_newline = False
    
    # We will build the new code
    new_code = []
    last_idx = 0
    
    while i < length:
        char = code[i]
        
        if escaped:
            escaped = False
            i += 1
            continue
            
        if state == 0:
            if char == '#':
                while i < length and code[i] != '\n':
                    i += 1
                continue
            elif char == '\'':
                if i + 2 < length and code[i+1] == '\'' and code[i+2] == '\'':
                    state = 3
                    i += 2
                else:
                    state = 1
                    current_string_start = i
                    contains_newline = False
            elif char == '"':
                if i + 2 < length and code[i+1] == '"' and code[i+2] == '"':
                    state = 4
                    i += 2
                else:
                    state = 2
                    current_string_start = i
                    contains_newline = False
        
        elif state == 1: # '
            if char == '\n':
                contains_newline = True
            elif char == '\\':
                escaped = True
            elif char == '\'':
                # Closing quote
                if contains_newline:
                    # Found a single quoted string with newline!
                    # Replace start and end with '''
                    # Output everything before start
                    new_code.append(code[last_idx:current_string_start])
                    new_code.append("'''")
                    # Output body
                    new_code.append(code[current_string_start+1:i])
                    new_code.append("'''")
                    last_idx = i + 1
                state = 0
                
        elif state == 2: # "
            if char == '\n':
                contains_newline = True
            elif char == '\\':
                escaped = True
            elif char == '"':
                # Closing quote
                if contains_newline:
                    # Found a double quoted string with newline!
                    # Replace start and end with """
                    new_code.append(code[last_idx:current_string_start])
                    new_code.append('"""')
                    new_code.append(code[current_string_start+1:i])
                    new_code.append('"""')
                    last_idx = i + 1
                state = 0
                
        elif state == 3: # '''
            if char == '\\':
                escaped = True
            elif char == '\'' and i + 2 < length and code[i+1] == '\'' and code[i+2] == '\'':
                state = 0
                i += 2
        
        elif state == 4: # """
            if char == '\\':
                escaped = True
            elif char == '"' and i + 2 < length and code[i+1] == '"' and code[i+2] == '"':
                state = 0
                i += 2
                
        i += 1
        
    # Handle open strings at EOF
    if (state == 1 or state == 2) and contains_newline:
        # We have an open string that contains a newline.
        # We must convert the start to triple quote and close it with triple quote.
        target_quote = "'''" if state == 1 else '"""'
        
        # We need to reconstruct the string with new start quote
        # The content starts at current_string_start + 1
        # The original start quote was at current_string_start
        
        # Append part before the string
        new_code.append(code[last_idx:current_string_start])
        # Append new start quote
        new_code.append(target_quote)
        # Append string content seen so far (from start+1 to end)
        new_code.append(code[current_string_start+1:])
        # Append closing quote
        new_code.append(target_quote)
        return "".join(new_code)

    # Append remainder if we didn't handle EOF specially above
    new_code.append(code[last_idx:])
    return "".join(new_code)

def _close_open_strings(code: str) -> str:
    """
    Scans code to detect if a string literal is left open at the end (truncated).
    Appends the necessary closing quote if so.
    """
    # Simple state machine
    # undefined state: 0
    # in ' : 1
    # in " : 2
    # in ''' : 3
    # in """ : 4
    
    state = 0
    escaped = False
    i = 0
    length = len(code)
    
    while i < length:
        char = code[i]
        
        if escaped:
            escaped = False
            i += 1
            continue
            
        if state == 0:
            # Not in string
            if char == '#':
                # Comment: skip until newline
                while i < length and code[i] != '\n':
                    i += 1
                continue
            elif char == '\'':
                # Check for triple
                if i + 2 < length and code[i+1] == '\'' and code[i+2] == '\'':
                    state = 3
                    i += 2
                else:
                    state = 1
            elif char == '"':
                # Check for triple
                if i + 2 < length and code[i+1] == '"' and code[i+2] == '"':
                    state = 4
                    i += 2
                else:
                    state = 2
        else:
            # In string
            if char == '\\':
                escaped = True
            elif state == 1: # '
                if char == '\'':
                    state = 0
            elif state == 2: # "
                if char == '"':
                    state = 0
            elif state == 3: # '''
                if char == '\'' and i + 2 < length and code[i+1] == '\'' and code[i+2] == '\'':
                    state = 0
                    i += 2
            elif state == 4: # """
                if char == '"' and i + 2 < length and code[i+1] == '"' and code[i+2] == '"':
                    state = 0
                    i += 2
        i += 1
        
    # Append closer if needed
    if state == 1:
        return code + "'"
    elif state == 2:
        return code + '"'
    elif state == 3:
        return code + "'''"
    elif state == 4:
        return code + '"""'
    return code


def _last_resort_backslash_fix(code: str) -> str:
    """
    If SyntaxError persists, aggressively strip trailing backslashes that are followed by whitespace.
    """
    lines = code.splitlines()
    new_lines = []
    for line in lines:
        # 1. Strip backslash + whitespace at EOL (common hallucination)
        line = re.sub(r'\\\s+$', '', line)
        
        # 2. Strip "rogue" backslashes in the middle of the line (e.g. r\blove -> rblove)
        line = re.sub(r'\\([^\n])', r'\1', line)
        
        new_lines.append(line)
    return "\n".join(new_lines)

def _repair_mashed_regex_print(code: str) -> str:
    """
    Heuristic to fix a very specific recurring LLM hallucination where it mashes:
    len(re.findall(r\\pattern print(...)))
    into a single line without quotes or newlines.
    
    Target: ... len(re.findall(r\blove print( ... )))
    Fix:    ... len(re.findall(r'\blove', text))\nprint( ... )
    
    We break the line into two statements.
    We guess the variable name 'text' for re.findall to prevent SyntaxError (NameError is better).
    """
    # Regex Explanation:
    # len\(re\.findall\(          -> matches literal start
    # (.+?)                       -> Capture Group 1: The regex pattern (non-greedy until print)
    # \s+print\(                  -> matches space and print(
    # (.*)                        -> Capture Group 2: The content inside print(...)
    # \)\)\)                      -> matches the closing parens
    
    def replacer(match):
        raw_pattern = match.group(1)
        print_content = match.group(2)
        
        # We need to wrap 'raw_pattern' in quotes properly.
        # If it looks like it starts with r, treat as r'...'.
        if raw_pattern.startswith('r') and len(raw_pattern) > 1:
             # simple heuristic: assume 'r' prefix is intentional.
             # e.g. raw_pattern="r\blove" -> result="r'\blove'"
             reconstructed_pattern = "r'" + raw_pattern[1:] + "'"
        else:
             # No r prefix, wrap in r'' to be safe
             reconstructed_pattern = "r'" + raw_pattern + "'"

        return f"len(re.findall({reconstructed_pattern}, text))\nprint({print_content})"

    code = re.sub(r'len\(re\.findall\((.+?)\s+print\((.*)\)\)\)', replacer, code)
    
    return code

def _repair_bareword_r_string(code: str) -> str:
    """
    Heuristic to fix bareword r-strings (missing quotes).
    Example: len(re.findall(r\blove\b, ...)) -> len(re.findall(r'\blove\b', ...))
    """
    # Regex: Capture 'r\' sequence at word boundary followed by non-separator chars
    pattern = r'\br\\([^\s,)]+)'
    
    def replacer(match):
        # match.group(1) is the content after r\
        # We assume the user meant r'...' 
        # We reconstruct it as r' \ content '
        # Original was r \ content.
        return f"r'\\{match.group(1)}'"
        
    return re.sub(pattern, replacer, code)

def fix_python_syntax(code: str) -> str:
    """
    Attempts to fix common Python syntax errors using a combination of regex and tokenization checks.
    """
    # 0. Brute-force cleanup
    code = re.sub(r'(\?[\w,]{1,3}){3,}', '', code) # Stuttering
    code = re.sub(r'(\?){3,}$', '', code) # Trailing ? sequence (stuttering)
    code = code.rstrip('`') # Trailing backticks at end of file
    
    # 0.1 Special Heuristic for current known hallucination
    code = _repair_mashed_regex_print(code)
    
    # 0.2 Fix bareword r-strings (must run before backslash stripping)
    code = _repair_bareword_r_string(code)

    if "\\n" in code: # forceful newline fix if it looks like a dump
         # Heuristic: If syntax is invalid, but unescaping \\n fixes it, do it.
         # This handles cases where the LLM output escaped text literals or JSON strings.
         try:
             # Try unescaping common sequences
             unescaped = code.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
             ast.parse(unescaped)
             # If valid, use it!
             code = unescaped
         except SyntaxError:
             pass

    # 1. Initial Parse Check
    try:
        ast.parse(code)
        return code
    except SyntaxError:
        pass

    # 2. Line-by-line Repair
    # 2.0.5 Force Unescape common sequences (fixes mashed newlines like 'import os\\nn')
    # This must happen BEFORE tokenization checks because \\n literals are often treated 
    # as valid tokens (backslash + n) but we want them to be Newlines.
    # Safe because we re-run _convert_multiline_strings (2.1.5) afterwards to fix broken strings.
    code = code.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')

    # 2.1 Attempt to repair invalid backslashes using tokenization first
    # This specifically targets "SyntaxError: unexpected character after line continuation character"
    # which standard regex struggles to perfectly isolate from valid strings.
    code = _repair_hallucinated_line_continuation(code)

    lines = code.splitlines()
    fixed_lines = [_repair_line(line) for line in lines]
    code = "\n".join(fixed_lines)
    
    # 2.1.5 Convert multi-line strings (SyntaxError) to triple quoted
    code = _convert_multiline_strings(code)

    # 2.2 Attempt to close open strings (truncated/hallucinated)
    # This must happen after regex repairs (which might have stripped escaped quotes incorrectly? 
    # No, our regex repairs tried to fix escaped EOL quotes. If they failed, this might help.)
    code = _close_open_strings(code)
    
    try:
        ast.parse(code)
        return code
    except SyntaxError as e:
        # 2.3 Last Resort for Line Continuation Errors
        # 2.3 Last Resort for Line Continuation Errors
        if "unexpected character after line continuation" in str(e):
             code = _last_resort_backslash_fix(code)
             # If we blindly unescaped \\n to \n in last resort, we might have created 
             # broken single-quoted strings containing newlines.
             # We must re-run the multiline string converter to fix them.
             code = _convert_multiline_strings(code)
    
    # Check again
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
            try:
                from .logging import pretty_log, Icons
                pretty_log("SANITIZATION FAILED", f"{e}\nCode:\n{content}", icon=Icons.BUG)
            except ImportError:
                print(f"[WARNING] SANITIZATION FAILED: {e}\nCode:\n{content}")
            return content, f"SyntaxError: {e}"
            
    return content, None
