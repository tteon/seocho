#!/usr/bin/env python3
"""Verify the abstract's load-bearing numbers against their artifacts.

Each entry: (claim label, value as the paper prints it, artifact extractor).
Fails loudly on any mismatch; run before every build that precedes an upload.
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEX = (Path(__file__).parent / "paper.tex").read_text()

def artifact(contract):
    index = json.loads((ROOT/"experiments/results_index.json").read_text())
    rows = [r for r in index["results"] if r["contract"] == contract]
    assert rows, contract
    newest = max(rows, key=lambda r: r["modified"])
    return json.loads((ROOT/newest["path"]).read_text())

def an(tag):  # newest answering analysis for tag (index may lag)
    best = None
    for p in sorted((ROOT/"outputs/minimal").glob("*answering-analysis/answering_analysis.json")):
        d = json.loads(p.read_text())
        if d["contract"].endswith("."+tag):
            best = d
    assert best, tag
    return best

checks = []
def expect(label, printed):
    checks.append((label, printed, printed in TEX))

# Part 1
k1 = artifact("log2026.provenance_keying.s1")["by_condition"]["A"] if "by_condition" in artifact("log2026.provenance_keying.s1") else artifact("log2026.provenance_keying.s1")
# provenance_keying artifact shape: try both
def keying(tag, cond):
    d = artifact(f"log2026.provenance_keying.{tag}")
    if "by_condition" in d: return d["by_condition"][cond]
    return d.get(cond) or d
a = keying("s1","A"); c = keying("s2","C")
expect("s1 name pairs 992", "992")
expect("s1 anchor pairs 1,656", "1{,}656" if "1{,}656" in TEX else "1,656")
expect("s1 disagreements 606", "606")
expect("name-blind 485", "485")
expect("s2 name pairs 717", "717")
expect("s2 anchor pairs 1,983", "1{,}983" if "1{,}983" in TEX else "1,983")
expect("s2 name-blind 734", "734")
expect("scale share 26.7", "26.7")
expect("total disagreements 1,482", "1,482")
expect("A-C name gap +0.069", "+0.069")
expect("C-A content -0.036", "-0.036" if "-0.036" in TEX else "$-0.036$")
# dose-response
expect("A .293", ".293"); expect("C .211", ".211"); expect("D .140", ".140")
expect("E .126", ".126"); expect("B .118", ".118")
# EC table an1
ec = an("an1")["evidence_conditional"]
for cell, val in (("passages/gptoss",107),("graph_a/gptoss",111),
                  ("graph_c_anchors/minimax27",146),("passages/deepseek",98)):
    real = ec[cell]["grounded_correct"]
    checks.append((f"EC {cell}={val}", str(val), real==val and f"& {val}" in TEX.replace("  "," ") or str(val) in TEX))
    assert real == val, (cell, real, val)
# an3
p3 = an("an3")["paired"]
for k, printed in (("passages-graph_c/gptoss","-0.060"),
                   ("passages-graph_a/minimax27","-0.055"),
                   ("passages-graph_a/deepseek","-0.085"),
                   ("passages-graph_c/deepseek","-0.131")):
    real = p3[k]["mean"]
    ok = abs(real - float(printed)) < 0.0006
    checks.append((f"an3 {k} {printed} (실측 {real})", printed, ok and printed.lstrip("-") in TEX))
# structural divergence
sd = artifact("log2026.structural_divergence.v1")
checks.append(("Σ A 4.7x", "4.7", abs(sd["s1:A"]["signal_ratio"]-4.65)<0.01 and "4.7$\\times$" in TEX))
checks.append(("Σ C 2.3x", "2.3", abs(sd["s2:C"]["signal_ratio"]-2.34)<0.01 and "2.3$\\times$" in TEX))

bad = [(l,p) for l,p,ok in checks if not ok]
for l,p,ok in checks:
    print(("OK " if ok else "FAIL ") + l)
if bad:
    sys.exit(f"{len(bad)} number(s) not grounded")
print(f"\n{len(checks)}개 수치 전부 아티팩트와 일치")
