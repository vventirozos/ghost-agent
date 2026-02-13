
import pytest
from ghost_agent.utils.sanitizer import sanitize_code

def test_sanitize_regression_indentation_error_handling():
    """
    Regression test for a bug where `tokenize` failing (e.g. IndentationError)
    caused the sanitizer to discard partial repairs of backslash errors.
    """
    # This code has TWO errors:
    # 1. Backslash followed by space (SyntaxError: unexpected character...) - The sanitizer SHOULD fix this.
    # 2. IndentationError (unexpected indent) - The sanitizer CANNOT fix this, but should not crash.
    
    # The crucial part is that the backslash error (which is obscure) gets fixed, 
    # leaving behind a standard IndentationError which is easier for the user/agent to understand.
    
    code = "  import os \\ \nprint('hi')"
    
    sanitized, error = sanitize_code(code, "test_regression.py")
    
    # We expect the backslash to be gone.
    # The output should roughly be: "  import os\nprint('hi')"
    assert "\\ " not in sanitized
    assert "import os" in sanitized
    
    # The error message should ideally reflect the remaining error (IndentationError)
    # or at least not be the original backslash error.
    # However, sanitize_code returns error string if ast.parse fails.
    assert error is not None
    # We want to ensure it's NOT "unexpected character after line continuation"
    assert "unexpected character after line continuation" not in error

def test_sanitize_regression_token_error_handling():
    """
    Test that TokenError (e.g. EOF in multi-line statement) also doesn't prevent partial fixes.
    """
    code = "x = [ \\ \n 1, 2" # Missing closing bracket -> TokenError
    
    sanitized, error = sanitize_code(code, "test_regression_token.py")
    
    # Backslash should be fixed despite the missing bracket
    assert "\\ " not in sanitized
    assert "x = [" in sanitized
