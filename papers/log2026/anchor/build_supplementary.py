#!/usr/bin/env python3
"""Build the anonymized supplementary zip for the anchor EA submission.

Contents: every dated registration, the hypothesis ledger, the claim map,
the judge protocol, the analysis entry points, the case lists, and the
contract-carrying artifact JSON for every number the abstract cites.
Refuses to package if any identity marker survives anonymization.
"""
import io, json, re, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent / "supplementary.zip"
MARKERS = re.compile(r"hadry|yitae|hardy\.jeong|xcena|/home/[a-z]+", re.I)

FILES = {
    "registrations": sorted((ROOT/"experiments/preregistration").glob("2026-08-0*.md")),
    "ledger": [ROOT/"papers/log2026/PREREGISTRATION.md",
               ROOT/"papers/log2026/anchor/CLAIM_MAP.md",
               ROOT/"papers/log2026/LLM_JUDGE_PROTOCOL.md"],
    "code": [ROOT/p for p in (
        "experiments/minimal/reextract.py", "experiments/minimal/arms.py",
        "experiments/minimal/provenance.py",
        "experiments/minimal/provenance_keying.py",
        "experiments/minimal/arm_results.py", "experiments/minimal/validity.py",
        "experiments/minimal/verification_value.py",
        "experiments/minimal/routing_ceiling.py",
        "experiments/answering.py", "experiments/answering_analysis.py",
        "experiments/structural_divergence.py",
        "experiments/disagreement_adjudication.py",
        "experiments/judge_panel.py", "experiments/select_arithmetic.py",
        "experiments/export_snapshots.py", "experiments/materialize_anchors.py",
        "examples/finder/datasets/neutral_meta_system_prompt.md")],
    "cases": sorted((ROOT/"dataset").glob("*_cases.txt")),
}

def artifacts():
    index = json.loads((ROOT/"experiments/results_index.json").read_text())
    wanted = ("arm_results", "validity", "provenance_keying",
              "verification_value", "routing_ceiling", "answering_analysis",
              "structural_divergence", "ma_adjudication",
              "fact_anchors_summary", "judge_calibration")
    rows = [r for r in index["results"]
            if any(w in r["contract"] for w in wanted)]
    newest = {}
    for r in sorted(rows, key=lambda r: r["modified"]):
        newest[r["contract"]] = ROOT / r["path"]
    return newest

def scrub(text: str) -> str:
    text = re.sub(r"/home/[a-z0-9_]+/[^\s\"']*seocho", "<REPO>", text)
    text = re.sub(r"/home/[a-z0-9_]+", "<HOME>", text)
    return text

def main():
    zf = zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED)
    manifest = {"claim_to_artifact": {}, "files": []}
    for group, paths in FILES.items():
        for p in paths:
            body = scrub(p.read_text(errors="replace"))
            assert not MARKERS.search(body), f"identity marker in {p}"
            zf.writestr(f"{group}/{p.name}", body)
            manifest["files"].append(f"{group}/{p.name}")
    for contract, p in artifacts().items():
        if not p.exists():
            continue
        body = scrub(p.read_text(errors="replace"))
        if MARKERS.search(body):
            raise SystemExit(f"identity marker in artifact {p}")
        name = f"artifacts/{contract}.json"
        zf.writestr(name, body)
        manifest["claim_to_artifact"][contract] = name
    zf.writestr("MANIFEST.json", json.dumps(manifest, indent=1))
    zf.close()
    print(f"{OUT.name}: {OUT.stat().st_size/1e6:.1f} MB, "
          f"{len(manifest['files'])} files + {len(manifest['claim_to_artifact'])} artifacts")

if __name__ == "__main__":
    main()
