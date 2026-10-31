"""Recognize obvious masking of test/build exits without executing shell text."""

import shlex


def masked_check(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False  # General shell syntax errors remain ordinary tool failures.
    check = False
    for index, token in enumerate(tokens):
        rest = []
        for part in tokens[index + 1 :]:
            if part and all(c in ";&|\n" for c in part):
                break
            rest.append(part)
        name = token.rsplit("/", 1)[-1]
        if name == "node" and any(p in {"--test", "--check"} for p in rest):
            check = True
        if name in {"npm", "npx"} and any(p in {"test", "build", "tsc", "vite"} for p in rest):
            check = True
        if name in {"tsc", "vite"}:
            check = True
    if not check:
        return False
    for index, token in enumerate(tokens):
        if token in {"echo", "printf", "head", "tail"}:
            return True
        if token and all(c in ";&|\n" for c in token):
            if token == "&&":
                continue
            if token == "&" and index and tokens[index - 1].endswith(">"):
                continue  # e.g. 2>&1 preserves a direct command's exit code.
            return True
    return False
