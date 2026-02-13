import re

def test_robust_regex():
    # Case 1: Simple word chars (previous)
    c1 = r'len(re.findall(r\blove print("foo")))'
    
    # Case 2: Symbols (caused failure?)
    c2 = r'len(re.findall(r\d+ print("foo")))'
    
    # Case 3: Groups
    c3 = r'len(re.findall(r\b(one|two) print("foo")))'
    
    # Case 4: No 'r' prefix? (Just to be safe)
    c4 = r'len(re.findall(\blove print("foo")))'
    
    # NEW Robust Regex
    # Matches: len(re.findall( ... print( ... )))
    # Group 1: The 'Pattern' blob (non-greedy until print)
    # Group 2: The 'Print' content
    
    robust_pattern = r'len\(re\.findall\((.+?)\s+print\((.*)\)\)\)'
    
    print(f"Regex: {robust_pattern}")
    
    for i, code in enumerate([c1, c2, c3, c4], 1):
        print(f"\n--- Case {i} ---")
        print(f"Code: {code}")
        match = re.search(robust_pattern, code)
        if match:
            raw_pattern = match.group(1)
            print_content = match.group(2)
            print(f"MATCH: pattern='{raw_pattern}', print='{print_content}'")
            
            # Reconstruction Logic
            # We need to wrap 'raw_pattern' in quotes properly.
            # If it starts with 'r', we prefer r'...'
            
            clean_pattern = raw_pattern
            # Handle r\ prefix collision from bad sanitization
            if clean_pattern.startswith(r"r\ ") or clean_pattern.startswith("r\\"):
                 # r\blove -> \blove (strip r\) and add r'...'
                 # Wait, clean_pattern might be "r\blove".
                 # We want r'\blove'.
                 pass
            
            # Simple fix strategy:
            # If it looks like it starts with r, treat as r'...'.
            if clean_pattern.startswith('r'):
                # Check if it has a backslash immediately? 
                # e.g. "r\blove" -> core is "\blove"
                # e.g. "rblove" -> core is "blove"
                # If we construct r'{clean_pattern}', we get r'r\blove'. Double r?
                # No. clean_pattern IS the raw text found.
                # If clean_pattern is "r\blove".
                # We want result: r'\blove'.
                # So we replace leading 'r' with "r'" and add end "'".
                 reconstructed_pattern = "r'" + clean_pattern[1:] + "'"
            else:
                # No r prefix, just wrap in r'' to be safe?
                reconstructed_pattern = "r'" + clean_pattern + "'"
            
            fixed = f"len(re.findall({reconstructed_pattern}, text))\nprint({print_content})"
            print(f"FIXED:\n{fixed}")
            
        else:
            print("NO MATCH.")

if __name__ == "__main__":
    test_robust_regex()
