import re
from pathlib import Path

def process_file(filepath: str):
    p = Path(filepath)
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8")

    lines = text.splitlines()
    new_lines = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # scripts/beads-path-guard.sh:87:    for raw in config_path.read_text().splitlines():
        if "for raw in config_path.read_text().splitlines():" in line and "beads-path-guard" in filepath:
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.append(f'{indent}with open(config_path, "r", encoding="utf-8") as f:')
            new_lines.append(f'{indent}    for raw in f:')
            i += 1
            while i < len(lines) and "def _discover_redirect_target" not in lines[i]:
                new_lines.append(f"    {lines[i]}")
                i += 1
            continue

        # scripts/gt-doctor.sh:156:    for raw in config_path.read_text().splitlines():
        elif "for raw in config_path.read_text().splitlines():" in line and "gt-doctor" in filepath:
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.append(f'{indent}with open(config_path, "r", encoding="utf-8") as f:')
            new_lines.append(f'{indent}    for raw in f:')
            i += 1
            while i < len(lines) and "def _load_issues_from_jsonl(" not in lines[i] and "return \"\"" not in lines[i]:
                new_lines.append(f"    {lines[i]}")
                i += 1
            if "return \"\"" in lines[i]:
                new_lines.append(lines[i])
                i += 1
            continue

        # scripts/gt-doctor.sh:231:    for line_no, raw in enumerate(issues_file.read_text().splitlines(), start=1):
        elif "for line_no, raw in enumerate(issues_file.read_text().splitlines(), start=1):" in line:
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.append(f'{indent}with open(issues_file, "r", encoding="utf-8") as f:')
            new_lines.append(f'{indent}    for line_no, raw in enumerate(f, start=1):')
            i += 1
            while i < len(lines) and "return rows, invalid_lines" not in lines[i]:
                new_lines.append(f"    {lines[i]}")
                i += 1
            new_lines.append(lines[i])
            i += 1
            continue

        # scripts/task-context-trail.sh:116:    for raw in path.read_text().splitlines():
        elif "for raw in path.read_text().splitlines():" in line:
            indent = line[:len(line) - len(line.lstrip())]
            new_lines.append(f'{indent}with open(path, "r", encoding="utf-8") as f:')
            new_lines.append(f'{indent}    for raw in f:')
            i += 1
            while i < len(lines) and "return events, invalid_lines" not in lines[i]:
                new_lines.append(f"    {lines[i]}")
                i += 1
            new_lines.append(lines[i])
            i += 1
            continue

        else:
            new_lines.append(line)
        i += 1

    p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

files_to_fix = [
    "scripts/beads-path-guard.sh",
    "scripts/gt-doctor.sh",
    "scripts/task-context-trail.sh"
]

for f in files_to_fix:
    process_file(f)
