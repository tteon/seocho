#!/usr/bin/env python3
"""Tier-1 operator labeling GUI (FastAPI, local, $0, no new heavy deps).

Replaces chat/CLI grading with a browser click-grid. First surface: POLARITY
grading of the grounded-correct HOLDS_POSITION positions (the direct-serve gate's
"polarity accuracy" half). Each item pre-selects the extracted polarity; the
grader only changes the wrong ones (or marks 'ambiguous' when the free-coined
topic has no clear direction — itself a finding). Grades persist to JSON and the
polarity accuracy is computed on save. NOT a from-scratch ontology editor (per
the ontologist panel: lean on YAML + draft→approve; this GUI only labels/reviews).

Run:  python examples/contextgraph/labeling_gui.py   # serves http://127.0.0.1:8765
Deps: fastapi + uvicorn (already in the stack). No streamlit/gradio.
"""
from __future__ import annotations
import csv, json, logging, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None:
        os.environ[k] = v
logging.getLogger("neo4j").setLevel(logging.ERROR)
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from seocho.store.graph import Neo4jGraphStore

DATA = ROOT / "examples/contextgraph/datasets/bc3_slices.csv"
DB = "cgbc3pos15d"
WS_PREFIX = "e1-bc3-pos15d-position-"
GRADES = ROOT / "outputs/evaluation/contextgraph/calibration/polarity_grades.json"
_STOP = {"the", "a", "an", "of", "to", "for", "at", "in", "on", "and", "or", "be", "we",
         "i", "is", "it", "this", "that", "s", "as", "are", "was", "if", "but", "so", "my"}


def _ctoks(s): return {t for t in re.findall(r"[a-z0-9]+", str(s).lower()) if t not in _STOP and len(t) > 2}
def _norm(s):
    s = str(s or "").strip()
    if s.count(",") == 1:
        a, b = s.split(",", 1); s = f"{b.strip()} {a.strip()}"
    return frozenset(t for t in re.findall(r"[a-z0-9]+", s.lower()) if t)
def _nmatch(a, b):
    sa, sb = _norm(a), _norm(b); return bool(sa) and bool(sb) and (sa == sb or sa <= sb or sb <= sa)
def _covers(stmt, q):
    g = _ctoks(stmt); return bool(g) and len(g & _ctoks(q)) / len(g) >= 0.5


def load_items():
    rows = [r for r in csv.DictReader(open(DATA)) if r["slice"] == "E4_POSITIONS"]
    gs = Neo4jGraphStore(os.environ["NEO4J_URI"], os.environ.get("NEO4J_USER", "neo4j"),
                         os.environ.get("NEO4J_PASSWORD", ""))
    items, seen = [], set()
    try:
        for r in rows:
            tid = str(r["_id"]).split("#")[0]; w = f"{WS_PREFIX}{tid}"
            ext = gs.query("MATCH (p:Person {_workspace_id:$w})-[rel:HOLDS_POSITION]->(t:Topic {_workspace_id:$w}) "
                           "RETURN p.name AS person, rel.polarity AS pol, rel.source_quote AS quote, t.name AS topic",
                           params={"w": w}, database=DB) or []
            if not ext:
                continue
            gold = [(s.split(":", 1)[0].strip(), s.split(":", 1)[1].strip()) for s in str(r["answer"]).split("|") if ":" in s]
            for e in ext:
                if any(_nmatch(a, e["person"]) and _covers(s, e.get("quote")) for a, s in gold):
                    k = (e["person"], e["pol"], str(e.get("quote"))[:60])
                    if k in seen:
                        continue
                    seen.add(k)
                    items.append({"id": len(items) + 1, "person": e["person"], "polarity": (e["pol"] or "").upper(),
                                  "topic": e["topic"], "quote": str(e.get("quote"))[:240]})
    finally:
        gs.close()
    return items


ITEMS = load_items()
app = FastAPI(title="SEOCHO labeling GUI")
_OPTS = ["FOR", "AGAINST", "NEUTRAL", "ambiguous"]


@app.get("/", response_class=HTMLResponse)
def index():
    saved = json.loads(GRADES.read_text()).get("grades", {}) if GRADES.exists() else {}
    rows = []
    for it in ITEMS:
        cur = saved.get(str(it["id"]), it["polarity"])
        opts = "".join(
            f'<label class=opt><input type=radio name="g{it["id"]}" value="{o}" '
            f'{"checked" if o == cur else ""}>{o}</label>' for o in _OPTS)
        rows.append(
            f'<tr><td>{it["id"]}</td><td><b>{it["person"]}</b><br><span class=topic>{it["topic"]}</span></td>'
            f'<td class=q>“{it["quote"]}”</td><td class=opts>{opts}</td></tr>')
    html = f"""<!doctype html><meta charset=utf-8><title>SEOCHO — polarity grading</title>
<style>body{{font:14px/1.5 system-ui;margin:24px;max-width:1100px}}table{{border-collapse:collapse;width:100%}}
td{{border-top:1px solid #ddd;padding:8px;vertical-align:top}}.topic{{color:#888;font-size:12px}}.q{{color:#225}}
.opts{{white-space:nowrap}}.opt{{margin-right:8px;font-size:12px}}#bar{{position:sticky;top:0;background:#fff;padding:8px 0}}
button{{padding:8px 16px;font-size:14px}}#out{{margin-left:16px;color:#070}}</style>
<h2>HOLDS_POSITION polarity grading <span style=color:#888>({len(ITEMS)} grounded-correct positions)</span></h2>
<p>각 항목의 polarity가 인용문에 비춰 맞는지 보세요. 틀리면 올바른 값으로, 토픽이 비방향적이면 <b>ambiguous</b>로. 저장하면 정확도가 계산됩니다.</p>
<div id=bar><button onclick=save()>Save grades</button><span id=out></span></div>
<table><tr><th>#</th><th>person / topic</th><th>quote</th><th>polarity</th></tr>{''.join(rows)}</table>
<script>
async function save(){{
 const g={{}};document.querySelectorAll('input[type=radio]:checked').forEach(r=>g[r.name.slice(1)]=r.value);
 const res=await fetch('/api/grades',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(g)}});
 const j=await res.json();
 document.getElementById('out').textContent=`saved ${{j.n}} · polarity accuracy ${{(j.accuracy*100).toFixed(0)}}% · ambiguous ${{j.ambiguous}} · changed ${{j.changed}}`;
}}
</script>"""
    return html


@app.post("/api/grades")
async def grades(request: Request):
    g = await request.json()
    orig = {str(it["id"]): it["polarity"] for it in ITEMS}
    decided = {k: v for k, v in g.items() if v in ("FOR", "AGAINST", "NEUTRAL")}
    correct = sum(1 for k, v in decided.items() if orig.get(k) == v)
    ambiguous = sum(1 for v in g.values() if v == "ambiguous")
    changed = sum(1 for k, v in g.items() if v != orig.get(k))
    acc = correct / len(decided) if decided else 0.0
    GRADES.parent.mkdir(parents=True, exist_ok=True)
    GRADES.write_text(json.dumps({"grades": g, "polarity_accuracy": acc, "n_decided": len(decided),
                                  "ambiguous": ambiguous, "changed": changed,
                                  "note": "accuracy = fraction of FOR/AGAINST/NEUTRAL items where extraction == human grade"}, indent=2))
    return JSONResponse({"n": len(g), "accuracy": acc, "ambiguous": ambiguous, "changed": changed})


if __name__ == "__main__":
    print(f"labeling GUI: http://127.0.0.1:8765  ({len(ITEMS)} items)  grades -> {GRADES}")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
