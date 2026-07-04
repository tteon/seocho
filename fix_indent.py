import os
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

        # We know we replaced `for raw in config_path.read_text().splitlines():` with:
        # with open(...) as f:
        #     for raw in f:

        # We need to find the `with open` loop we inserted and indent its body by 4 spaces until the end of the loop body.

        # For beads-path-guard.sh
        if "def _read_redirect_from_config(" in line:
            new_lines.append(line)
            i += 1
            while i < len(lines) and "def _discover_redirect_target(" not in lines[i]:
                # 87:    with open(config_path, "r", encoding="utf-8") as f:
                # 88:        for raw in f:
                # 89:        line = raw.strip()
                if "line = raw.strip()" in lines[i] and "        line = raw.strip()" == lines[i]:
                    new_lines.append("            line = raw.strip()")
                elif "if not line or line.startswith(\"#\"):" in lines[i] and "        if not line" == lines[i][:19]:
                    new_lines.append("            if not line or line.startswith(\"#\"):")
                elif "continue" in lines[i] and "            continue" == lines[i]:
                    new_lines.append("                continue")
                elif "if line.startswith(\"redirect:\"):" in lines[i] and "        if line.startswith" == lines[i][:26]:
                    new_lines.append("            if line.startswith(\"redirect:\"):")
                elif "return line.split(\":\", 1)[1]" in lines[i] and "            return line.split" == lines[i][:29]:
                    new_lines.append("                return line.split(\":\", 1)[1].strip().strip(\"'\\\"\")")
                else:
                    new_lines.append(lines[i])
                i += 1
            continue

        # For gt-doctor.sh - same function `def _read_redirect_from_config(`
        if "def _load_issues_from_jsonl(" in line:
            new_lines.append(line)
            i += 1
            while i < len(lines) and "def " not in lines[i] and "result: dict" not in lines[i] and "if json_output" not in lines[i]:
                if "line = raw.strip()" in lines[i] and "        line = raw.strip()" == lines[i]:
                    new_lines.append("            line = raw.strip()")
                elif "if not line:" in lines[i] and "        if not line:" == lines[i]:
                    new_lines.append("            if not line:")
                elif "continue" in lines[i] and "            continue" == lines[i]:
                    new_lines.append("                continue")
                elif "try:" in lines[i] and "        try:" == lines[i]:
                    new_lines.append("            try:")
                elif "parsed = json.loads(line)" in lines[i] and "            parsed = json.loads(line)" == lines[i]:
                    new_lines.append("                parsed = json.loads(line)")
                elif "if \"id\" not in parsed:" in lines[i] and "            if \"id\" not in parsed:" == lines[i]:
                    new_lines.append("                if \"id\" not in parsed:")
                elif "raise ValueError" in lines[i] and "                raise ValueError" == lines[i][:32]:
                    new_lines.append(f"    {lines[i]}")
                elif "parsed[\"_line_no\"] = line_no" in lines[i] and "            parsed[\"_line_no\"] = line_no" == lines[i]:
                    new_lines.append("                parsed[\"_line_no\"] = line_no")
                elif "rows.append(parsed)" in lines[i] and "            rows.append(parsed)" == lines[i]:
                    new_lines.append("                rows.append(parsed)")
                elif "except json.JSONDecodeError:" in lines[i] and "        except json.JSONDecodeError:" == lines[i]:
                    new_lines.append("            except json.JSONDecodeError:")
                elif "invalid_lines += 1" in lines[i] and "            invalid_lines += 1" == lines[i]:
                    new_lines.append("                invalid_lines += 1")
                else:
                    new_lines.append(lines[i])
                i += 1
            continue

        # For task-context-trail.sh
        if "def _load_events(" in line:
            new_lines.append(line)
            i += 1
            while i < len(lines) and "def _match_event(" not in lines[i]:
                if "line = raw.strip()" in lines[i] and "        line = raw.strip()" == lines[i]:
                    new_lines.append("            line = raw.strip()")
                elif "if not line:" in lines[i] and "        if not line:" == lines[i]:
                    new_lines.append("            if not line:")
                elif "continue" in lines[i] and "            continue" == lines[i]:
                    new_lines.append("                continue")
                elif "try:" in lines[i] and "        try:" == lines[i]:
                    new_lines.append("            try:")
                elif "event = json.loads(line)" in lines[i] and "            event = json.loads(line)" == lines[i]:
                    new_lines.append("                event = json.loads(line)")
                elif "if event.get(\"task_id\") == task_id:" in lines[i] and "            if event.get(\"task_id\") == task_id:" == lines[i]:
                    new_lines.append("                if event.get(\"task_id\") == task_id:")
                elif "events.append(event)" in lines[i] and "                events.append(event)" == lines[i]:
                    new_lines.append("                    events.append(event)")
                elif "except json.JSONDecodeError:" in lines[i] and "        except json.JSONDecodeError:" == lines[i]:
                    new_lines.append("            except json.JSONDecodeError:")
                elif "invalid_lines += 1" in lines[i] and "            invalid_lines += 1" == lines[i]:
                    new_lines.append("                invalid_lines += 1")
                else:
                    new_lines.append(lines[i])
                i += 1
            continue

        new_lines.append(line)
        i += 1

    p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

files = [
    "scripts/beads-path-guard.sh",
    "scripts/gt-doctor.sh",
    "scripts/task-context-trail.sh"
]

for f in files:
    process_file(f)
