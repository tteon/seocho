#!/usr/bin/env python3
"""Case-blocked serial mediation associations for the 256-cell factorial gate."""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "outputs/evaluation/mdm_fedcat/log2026-factorial-mediation-v1/answers.json"
OUT = ROOT / "outputs/evaluation/mdm_fedcat/log2026-factorial-mediation-v1/mediation.json"


def z(values):
    array=np.asarray(values,dtype=float); std=array.std(); return (array-array.mean())/std if std else np.zeros_like(array)
def dummies(values):
    levels=sorted(set(values)); return [np.array([float(v==level) for v in values]) for level in levels[1:]]
def coefficient(y, columns, index):
    x=np.column_stack([np.ones(len(y)),*columns]); return float(np.linalg.lstsq(x,np.asarray(y),rcond=None)[0][index+1])


def paths(rows):
    case=[r["case_id"] for r in rows]; provider=[r["provider_id"] for r in rows]
    prompt=np.array([float(r["prompt_level"]=="task_specific") for r in rows]); ontology=np.array([float(r["ontology_level"]=="finance") for r in rows]); interaction=prompt*ontology
    controls=[*dummies(case),*dummies(provider)]; graph=z([float(r["nodes_created"])+float(r["rels_created"]) for r in rows]); retrieval=z([r["retrieval_token_recall"] for r in rows]); answer=z([0 if r["response"].get("parse_error") else r["answer_token_f1"] for r in rows])
    results={}
    for name,treatment,other in (("prompt",prompt,ontology),("ontology",ontology,prompt)):
        base=[treatment,other,interaction,*controls]
        a=coefficient(graph,base,0)
        d=coefficient(retrieval,[graph,*base],0)
        b=coefficient(answer,[retrieval,graph,*base],0)
        total=coefficient(answer,base,0)
        direct=coefficient(answer,[graph,retrieval,*base],2)
        results[name]={"treatment_to_graph":a,"graph_to_retrieval":d,"retrieval_to_answer":b,
                       "serial_indirect":a*d*b,"total_association":total,"direct_with_mediators":direct}
    return results


def main()->int:
    rows=[r for r in json.loads(SOURCE.read_text())["rows"] if not r.get("runtime_error")]
    point=paths(rows); by={case:[r for r in rows if r["case_id"]==case] for case in sorted({r["case_id"] for r in rows})}; cases=list(by); rng=random.Random(20260712)
    boots={t:{k:[] for k in values} for t,values in point.items()}
    for _ in range(2000):
        sample=[]
        for index,case in enumerate(rng.choices(cases,k=len(cases))): sample.extend({**r,"case_id":f"{case}#{index}"} for r in by[case])
        result=paths(sample)
        for treatment,values in result.items():
            for key,value in values.items():boots[treatment][key].append(value)
    output={}
    for treatment,values in point.items():
        output[treatment]={}
        for key,value in values.items():
            ordered=sorted(boots[treatment][key]);output[treatment][key]={"estimate":round(value,6),"case_clustered_bootstrap_95_ci":[round(ordered[50],6),round(ordered[1950],6)]}
    payload={"contract":"log2026.factorial_mediation.v1","cases":16,"cells":len(rows),
             "scope":"standardized serial mediation association in the orthogonal calibration gate; not a causal effect and not full-corpus performance",
             "path":"binary prompt or ontology treatment -> z(nodes+relations) -> z(retrieval token recall) -> z(answer token F1)",
             "controls":["FinDER case fixed effects","generation-model fixed effects","other treatment","prompt-by-ontology interaction"],"results":output}
    OUT.write_text(json.dumps(payload,indent=2)+"\n");print(json.dumps(output,indent=2));return 0


if __name__=="__main__":raise SystemExit(main())
