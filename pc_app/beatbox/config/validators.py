import ast

from configobj.validate import ValidateError


def list_w_choice(value, *choices):
    # Support both args and kwargs for 'choices'

    # If it's a string (e.g., "('l', 'r')"), parse it into a tuple
    if isinstance(choices, str):
        try:
            choices = ast.literal_eval(choices)
        except Exception:
            raise ValidateError(f"Could not parse choices: {choices}")

    # Validate each item
    for item in value:
        if item not in choices:
            raise ValidateError(f"Invalid item '{item}', expected one of {choices}")

    return value
