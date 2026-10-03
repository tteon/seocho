from pathlib import Path

# Fix e2e_axiom_ab.py
p = Path("scripts/agentos/e2e_axiom_ab.py")
text = p.read_text()
search = """            with envf.open('r', encoding='utf-8') as f:
                for line in f:
                if line.strip() and not line.startswith("#") and "=" in line:"""
replace = """            with envf.open('r', encoding='utf-8') as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:"""
p.write_text(text.replace(search, replace))

# Fix e2e_cold_start_ab.py
p = Path("scripts/agentos/e2e_cold_start_ab.py")
text = p.read_text()
search = """            with envf.open('r', encoding='utf-8') as f:
                for line in f:
                if line.strip() and not line.startswith("#") and "=" in line:"""
replace = """            with envf.open('r', encoding='utf-8') as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:"""
p.write_text(text.replace(search, replace))

# Fix e2e_cross_model_intern.py
p = Path("scripts/agentos/e2e_cross_model_intern.py")
text = p.read_text()
search = """            with envf.open('r', encoding='utf-8') as f:
                for line in f:
                if line.strip() and not line.startswith("#") and "=" in line:"""
replace = """            with envf.open('r', encoding='utf-8') as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:"""
p.write_text(text.replace(search, replace))

# Fix e2e_ontology_source_ab.py
p = Path("scripts/agentos/e2e_ontology_source_ab.py")
text = p.read_text()
search = """            with envf.open('r', encoding='utf-8') as f:
                for line in f:
                if line.strip() and not line.startswith("#") and "=" in line:"""
replace = """            with envf.open('r', encoding='utf-8') as f:
                for line in f:
                    if line.strip() and not line.startswith("#") and "=" in line:"""
p.write_text(text.replace(search, replace))


# Fix results_log.py
p = Path("scripts/finbench/results_log.py")
text = p.read_text()
search = """        with ledger.open('r', encoding='utf-8') as f:
            for line in f:
            if not line.strip():
                continue
            e = json.loads(line)"""
replace = """        with ledger.open('r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                e = json.loads(line)"""
p.write_text(text.replace(search, replace))

# Fix trailing newlines in test_serving_image_hardening.py
p = Path("tests/seocho/test_serving_image_hardening.py")
text = p.read_text()
search = """    with ENTRYPOINT.open("r", encoding="utf-8") as f:
        return "\\n".join(
            line for line in f
            if not line.lstrip().startswith("#")
        )"""
replace = """    with ENTRYPOINT.open("r", encoding="utf-8") as f:
        return "\\n".join(
            line.rstrip("\\n") for line in f
            if not line.lstrip().startswith("#")
        )"""
p.write_text(text.replace(search, replace))
print("Fixed indentation and newlines.")
