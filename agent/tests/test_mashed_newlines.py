import pytest
from ghost_agent.utils.sanitizer import sanitize_code

def test_mashed_newlines_import():
    # Simulate code where newlines are escaped as literal \n characters
    # This causes _last_resort_backslash_fix to turn \n into n
    
    # "import os\n\n# Comment" (where \n are characters \ and n)
    bad_code = "import os\\n\\n# Comment"
    sanitized, err = sanitize_code(bad_code, "test.py")
    
    print(f"Original: {bad_code!r}")
    print(f"Sanitized: {sanitized!r}")
    
    # We expect "import os\n\n# Comment" or similar valid code
    # Currently we expect failure: "import osnn# Comment"
    assert "import osnn" not in sanitized
    assert "import os" in sanitized

def test_mashed_newlines_print():
    # "print('hello')\nprint('world')"
    bad_code = "print('hello')\\nprint('world')"
    sanitized, err = sanitize_code(bad_code, "test.py")
    
    print(f"Sanitized: {sanitized!r}")
    assert "print('hello')\nprint('world')" in sanitized

def test_mashed_string_literal():
    # Case: String with literal \n chars in a file that HAS other syntax errors.
    # We want to ensure that when we unescape the file to fix the error, 
    # we don't accidentally break string literals.
    # Input: "x = 1 \\ " (SyntaxError) + "s = 'foo\\nbar'" (Valid).
    # Repair: Unescapes ALL. "x=1 \ " (validish) + "s='foo\nbar'" (broken).
    # Then fixes string "s='''foo\nbar'''".
    bad_code = "x = 1 \\ \ns = 'foo\\nbar'"
    sanitized, err = sanitize_code(bad_code, "test.py")
    
    # Assert result is valid python
    assert "'''" in sanitized or '"""' in sanitized, "Should convert to triple quotes"
    assert "foo\nbar" in sanitized, "Should contain actual newline"
