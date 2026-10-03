from pathlib import Path

# Fix test_env_contract.py
p = Path("tests/seocho/test_env_contract.py")
text = p.read_text()
search = """    with (ROOT / ".env.example").open("r", encoding="utf-8") as f:
        lines = f.readlines()"""
replace = """    with (ROOT / ".env.example").open("r", encoding="utf-8") as f:
        lines = [line.rstrip("\\n") for line in f]"""
p.write_text(text.replace(search, replace))

print("Fixed newlines in env_contract.")
