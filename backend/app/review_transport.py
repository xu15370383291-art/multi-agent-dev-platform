"""Lossless transport for full-file additions already present in source.files."""

import hashlib
import re


def source_view(source):
    diff = source.get("diff")
    if not isinstance(diff, str) or not isinstance(source.get("files"), dict):
        return source
    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    result, references = [], []
    for section in sections:
        lines = section.splitlines(keepends=True)
        match = re.match(r"diff --git a/([^\s]+) b/([^\s]+)\r?\n", section)
        if not match or match[1] != match[2]:
            result.append(section)
            continue
        path = match[1]
        text = source["files"].get(path)
        hunk = next((i for i, line in enumerate(lines) if line.startswith("@@ ")), None)
        if (
            not isinstance(text, str)
            or hunk is None
            or not any(line.startswith("new file mode ") for line in lines[:hunk])
            or "--- /dev/null\n" not in lines[:hunk]
            or "+++ b/" + path + "\n" not in lines[:hunk]
            or not re.fullmatch(r"@@ -0,0 \+1(?:,\d+)? @@\r?\n", lines[hunk])
        ):
            result.append(section)
            continue
        body = lines[hunk + 1 :]
        no_newline = bool(body and body[-1].rstrip("\r\n") == "\\ No newline at end of file")
        if no_newline:
            body = body[:-1]
        if not body or any(not line.startswith("+") for line in body):
            result.append(section)
            continue
        count = re.fullmatch(r"@@ -0,0 \+1(?:,(\d+))? @@\r?\n", lines[hunk])
        if int(count[1] or 1) != len(body):
            result.append(section)
            continue
        restored = "".join(line[1:] for line in body)
        if no_newline:
            restored = restored.removesuffix("\n")
        if restored != text:
            result.append(section)
            continue
        # Full bytes still exist at this path, with original line numbers.
        reference = {
            "path": path,
            "source": "source.files",
            "lines": len(text.splitlines()),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        references.append(reference)
        result.append(
            "".join(lines[:hunk])
            + "Full-file addition: see source.files["
            + repr(path)
            + "]; original lines unchanged.\n"
        )
    if not references:
        return source
    return {
        **source,
        "diff": "".join(result),
        "diff_references": references,
        "diff_transport_contract": "exact-full-file-addition-v1",
    }
