import re

def test_regex():
    # Exact string from the log (assuming copy-paste accuracy)
    # Note: 'rblove' means the backslash was stripped by a later pass, 
    # so the input to _repair_mashed_regex_print (which runs early) likely had 'r\blove'.
    # I will test BOTH versions.
    
    code_stripped = 'love_count = len(re.findall(rblove print(f"Love appears {love_count} times in the text." )))'
    code_original = r'love_count = len(re.findall(r\blove print(f"Love appears {love_count} times in the text." )))'
    
    # My current regex
    # len\(re\.findall\((?:r\\?|r)(\w+)\s+print\((.*)\)\)\)
    pattern = r'len\(re\.findall\((?:r\\?|r)(\w+)\s+print\((.*)\)\)\)'
    
    print(f"Regex: {pattern}")
    
    print(f"\nTesting Stripped: {code_stripped}")
    match = re.search(pattern, code_stripped)
    if match:
        print("MATCHED!")
        print(f"Groups: {match.groups()}")
    else:
        print("NO MATCH.")

    print(f"\nTesting Original: {code_original}")
    match = re.search(pattern, code_original)
    if match:
        print("MATCHED!")
        print(f"Groups: {match.groups()}")
    else:
        print("NO MATCH.")

if __name__ == "__main__":
    test_regex()
