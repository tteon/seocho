import os
from pathlib import Path

def patch_file(filepath):
    print(f"Patching {filepath}...")
    content = Path(filepath).read_text()
    if "read_text().splitlines()" in content:
        if filepath.endswith("test_serving_image_hardening.py") or filepath.endswith("test_env_contract.py") or filepath.endswith("02_fibo_module_impact.ipynb"):
            # skip these
            return

        lines = content.splitlines(keepends=True)
        new_lines = []
        for line in lines:
            if "read_text().splitlines()" in line:
                if "return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]" in line:
                    indent = line[:len(line) - len(line.lstrip())]
                    new_lines.append(f"{indent}with path.open('r', encoding='utf-8') as f:\n")
                    new_lines.append(f"{indent}    return [json.loads(line) for line in f if line.strip()]\n")
                elif "for line in envf.read_text().splitlines():" in line:
                    indent = line[:len(line) - len(line.lstrip())]
                    new_lines.append(f"{indent}with envf.open('r', encoding='utf-8') as f:\n")
                    new_lines.append(f"{indent}    for line in f:\n")
                elif "rows = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]" in line:
                    indent = line[:len(line) - len(line.lstrip())]
                    new_lines.append(f"{indent}with out_path.open('r', encoding='utf-8') as f:\n")
                    new_lines.append(f"{indent}    rows = [json.loads(line) for line in f if line.strip()]\n")
                elif "for line in ledger.read_text().splitlines():" in line:
                    indent = line[:len(line) - len(line.lstrip())]
                    new_lines.append(f"{indent}with ledger.open('r', encoding='utf-8') as f:\n")
                    new_lines.append(f"{indent}    for line in f:\n")
                elif "entries = [json.loads(l) for l in ledger.read_text().splitlines() if l.strip()]" in line:
                    indent = line[:len(line) - len(line.lstrip())]
                    new_lines.append(f"{indent}with ledger.open('r', encoding='utf-8') as f:\n")
                    new_lines.append(f"{indent}    entries = [json.loads(l) for l in f if l.strip()]\n")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Fix indentation for inner blocks
        fixed_lines = []
        in_with = False
        with_indent = ""
        for i, line in enumerate(new_lines):
            if "with envf.open('r', encoding='utf-8') as f:" in line or "with ledger.open('r', encoding='utf-8') as f:" in line:
                if "for line in f:" in new_lines[i+1]:
                    in_with = True
                    with_indent = line[:len(line) - len(line.lstrip())]

            if in_with and line != new_lines[i]:
                if line.strip() == "":
                    fixed_lines.append(line)
                elif line.startswith(with_indent) and not line.startswith(with_indent + "    ") and i > i + 1:
                    in_with = False
                    fixed_lines.append(line)
                elif i > 0 and ("with envf.open" in new_lines[i-1] or "with ledger.open" in new_lines[i-1]):
                     fixed_lines.append(line)
                elif i > 1 and ("with envf.open" in new_lines[i-2] or "with ledger.open" in new_lines[i-2]):
                     fixed_lines.append(f"    {line}")
                elif i > 2 and ("with envf.open" in new_lines[i-3] or "with ledger.open" in new_lines[i-3]):
                     fixed_lines.append(f"    {line}")
                elif i > 3 and ("with envf.open" in new_lines[i-4] or "with ledger.open" in new_lines[i-4]):
                     fixed_lines.append(f"    {line}")
                elif i > 4 and ("with envf.open" in new_lines[i-5] or "with ledger.open" in new_lines[i-5]):
                     fixed_lines.append(f"    {line}")
                elif i > 5 and ("with envf.open" in new_lines[i-6] or "with ledger.open" in new_lines[i-6]):
                     fixed_lines.append(f"    {line}")
                elif i > 6 and ("with envf.open" in new_lines[i-7] or "with ledger.open" in new_lines[i-7]):
                     fixed_lines.append(f"    {line}")
                elif i > 7 and ("with envf.open" in new_lines[i-8] or "with ledger.open" in new_lines[i-8]):
                     fixed_lines.append(f"    {line}")
                elif i > 8 and ("with envf.open" in new_lines[i-9] or "with ledger.open" in new_lines[i-9]):
                     fixed_lines.append(f"    {line}")
                elif i > 9 and ("with envf.open" in new_lines[i-10] or "with ledger.open" in new_lines[i-10]):
                     fixed_lines.append(f"    {line}")
                elif i > 10 and ("with envf.open" in new_lines[i-11] or "with ledger.open" in new_lines[i-11]):
                     fixed_lines.append(f"    {line}")
                elif i > 11 and ("with envf.open" in new_lines[i-12] or "with ledger.open" in new_lines[i-12]):
                     fixed_lines.append(f"    {line}")
                elif i > 12 and ("with envf.open" in new_lines[i-13] or "with ledger.open" in new_lines[i-13]):
                     fixed_lines.append(f"    {line}")
                elif i > 13 and ("with envf.open" in new_lines[i-14] or "with ledger.open" in new_lines[i-14]):
                     fixed_lines.append(f"    {line}")
                else:
                    fixed_lines.append(line)

            else:
                fixed_lines.append(line)

        Path(filepath).write_text("".join(fixed_lines))


for root, _, files in os.walk("scripts"):
    for file in files:
        if file.endswith(".py"):
            filepath = os.path.join(root, file)
            patch_file(filepath)
