from pathlib import Path

# Revert JSONDecodeError handling in scripts/benchmarks/okx_release_gate.py to preserve original crash behavior
f = Path("scripts/benchmarks/okx_release_gate.py")
content = f.read_text()
content = content.replace(
    '''    vertical = {}
    if vertical_path.exists():
        try:
            with vertical_path.open() as f:
                vertical = json.load(f)
        except json.JSONDecodeError:
            pass
    chaos = {}
    if chaos_path.exists():
        try:
            with chaos_path.open() as f:
                chaos = json.load(f)
        except json.JSONDecodeError:
            pass''',
    '''    vertical = {}
    if vertical_path.exists():
        with vertical_path.open() as f:
            vertical = json.load(f)
    chaos = {}
    if chaos_path.exists():
        with chaos_path.open() as f:
            chaos = json.load(f)'''
)
f.write_text(content)
