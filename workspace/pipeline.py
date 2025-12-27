import os
import json
import hashlib
import pickle
import csv
from pathlib import Path
from typing import Dict, List
from dotenv import load_dotenv
from openai import OpenAI
import opik
from opik import Opik, track
from opik.opik_context import update_current_trace
from tqdm import tqdm  # 진행률 표시용

# ==========================================
# 1. 환경 설정 & 캐싱
# ==========================================
load_dotenv()

os.environ["OPIK_URL_OVERRIDE"] = os.getenv("OPIK_BASE_URL", "http://localhost:5173/api")
os.environ["OPIK_WORKSPACE"] = os.getenv("OPIK_WORKSPACE", "seocho-kgbuild")
os.environ["OPIK_PROJECT_NAME"] = os.getenv("OPIK_PROJECT_NAME", "kgbuild")

CACHE_DIR = Path("./.openai_cache")
CACHE_DIR.mkdir(exist_ok=True)

RDF_OUT_DIR = Path("./output/rdf_n10s")
LPG_OUT_DIR = Path("./output/lpg_native")
RDF_OUT_DIR.mkdir(parents=True, exist_ok=True)
LPG_OUT_DIR.mkdir(parents=True, exist_ok=True)

try:
    OPIK_CLIENT = Opik()
    OPENAI_CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    print(f"✅ Clients Initialized.")
except Exception as e:
    print(f"❌ Init Failed: {e}")
    exit(1)

def cached_chat_completion(model, messages, response_format=None, temperature=0.0):
    """Local Disk Caching"""
    raw_key = f"{model}_{json.dumps(messages, sort_keys=True)}_{temperature}"
    key_hash = hashlib.md5(raw_key.encode("utf-8")).hexdigest()
    cache_path = CACHE_DIR / f"{key_hash}.pkl"

    if cache_path.exists():
        with open(cache_path, "rb") as f:
            return pickle.load(f)

    response = OPENAI_CLIENT.chat.completions.create(
        model=model,
        messages=messages,
        response_format=response_format,
        temperature=temperature
    )
    
    with open(cache_path, "wb") as f:
        pickle.dump(response, f)
    
    return response

# ==========================================
# 2. Prompt & Ontology Logic
# ==========================================
def get_fibo_snippet(text):
    text_lower = text.lower()
    snippet = "Base Ontology: Financial Industry Business Ontology (FIBO)\n"
    if "bond" in text_lower or "debt" in text_lower:
        snippet += """
        Domain: FBC/FinancialInstruments/Debt
        - Class: fibo-fbc-fi-fi:DebtInstrument
        - Class: fibo-fbc-fi-fi:Bond (SubClassOf: DebtInstrument)
        - Property: fibo-fbc-fi-fi:hasPrincipalAmount (Range: MonetaryAmount)
        - Property: fibo-fbc-fi-fi:hasMaturityDate (Range: xsd:date)
        """
    if "share" in text_lower or "equity" in text_lower:
        snippet += """
        Domain: FBC/FinancialInstruments/Equity
        - Class: fibo-fbc-fi-fi:Share
        - Property: fibo-fbc-fi-fi:hasVotingRight (Range: boolean)
        """
    if "issued" in text_lower or "corp" in text_lower:
        snippet += """
        Domain: BE/LegalEntities/LegalPersons
        - Class: fibo-be-le-lp:LegalEntity
        - Property: fibo-fnd-rel-rel:hasName (Range: xsd:string)
        - Property: fibo-be-le-lp:isDomiciledIn (Range: Country)
        """
    return snippet

def setup_prompts():
    """Register Prompts to Opik (Critical)"""
    
    system_instruction = """You are a Principal Financial Knowledge Engineer specializing in FIBO.
Your task is to extract a "Hybrid Knowledge Graph".

### 1. RDF View (Strict Schema)
* **Goal:** Create high-fidelity triples based *only* on the provided Ontology Snippet.
* **Rules:**
    * Classes/Properties must match the snippet exactly.
    * Use consistent URIs (e.g., `ex:USDEntity`).
    * Do NOT invent classes.

### 2. LPG View (Rich Context)
* **Goal:** Capture semantic richness.
* **Rules:**
    * **Node IDs:** MUST match RDF Subject URIs.
    * **Properties:** Include schema-less attributes (risk, sentiment).
    * **Relationships:** Create intuitive edges.

### 3. Execution Strategy
1. Identify Entities.
2. Assign IDs.
3. Map to FIBO (RDF).
4. Enrich (LPG).
"""
    user_template = """
Analyze the Input Text using the provided Ontology Snippet.

# Input Text
{{text}}

# Ontology Snippet (FIBO Guidelines)
{{ontology_snippet}}

# Output Format (JSON)
{{
  "rdf_triples": [
    {{
      "subject": "ex:EntityURI",
      "predicate": "prefix:propertyName",
      "object": "ex:Value",
      "is_literal": true
    }}
  ],
  "lpg_graph": {{
    "nodes": [
      {{ "id": "ex:EntityURI", "label": "Class", "properties": {{ "name": "..." }} }}
    ],
    "relationships": [
      {{ "source": "ex:A", "target": "ex:B", "type": "REL", "properties": {{}} }}
    ]
  }}
}}
"""
    OPIK_CLIENT.create_chat_prompt(
        name="fibo-hybrid-extractor",
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_template}
        ],
        metadata={"version": "v4-principal", "strategy": "RDF+LPG"}
    )

    OPIK_CLIENT.create_prompt(
        name="fibo-grounder",
        prompt="""Verify if the extracted fact is supported by the text. Return 'TRUE' or 'FALSE'.
Source: {{text}}
Fact: <{{subject}}, {{predicate}}, {{object}}>""",
        metadata={"version": "v1-boolean-check"}
    )
    print("✅ Prompts successfully registered.")

# ==========================================
# 3. Pipeline Logic
# ==========================================

@track(name="step_extraction")
def step_extraction(raw_text: str):
    snippet = get_fibo_snippet(raw_text)
    
    try:
        prompt_template = OPIK_CLIENT.get_chat_prompt(name="fibo-hybrid-extractor")
    except Exception:
        setup_prompts()
        prompt_template = OPIK_CLIENT.get_chat_prompt(name="fibo-hybrid-extractor")

    if prompt_template is None:
        return {}, None, snippet

    messages = prompt_template.format(variables={
        "text": raw_text,
        "ontology_snippet": snippet
    })
    
    response = cached_chat_completion(
        model="gpt-4o",
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0.0
    )
    
    try:
        return json.loads(response.choices[0].message.content), prompt_template, snippet
    except:
        return {"rdf_triples": [], "lpg_graph": {}}, prompt_template, snippet

@track(name="step_grounding")
def step_grounding(raw_text: str, rdf_triples: List[Dict]):
    try:
        grounder_prompt = OPIK_CLIENT.get_prompt(name="fibo-grounder")
    except:
        return rdf_triples 

    verified_triples = []
    for triple in rdf_triples:
        formatted_prompt = grounder_prompt.format(
            text=raw_text,
            subject=triple.get("subject"),
            predicate=triple.get("predicate"),
            object=triple.get("object")
        )
        response = cached_chat_completion(
            model="gpt-4o-mini", 
            messages=[{"role": "user", "content": formatted_prompt}],
            temperature=0.0
        )
        if "TRUE" in response.choices[0].message.content.strip().upper():
            verified_triples.append(triple)
            
    return verified_triples

@track(name="fibo-main-pipeline")
def run_fibo_pipeline(input_text: str):
    extraction_result, extract_prompt, used_snippet = step_extraction(input_text)
    
    raw_rdf = extraction_result.get("rdf_triples", [])
    lpg_graph = extraction_result.get("lpg_graph", {"nodes": [], "relationships": []})
    
    verified_rdf = step_grounding(input_text, raw_rdf)
    
    update_current_trace(
        prompts=[extract_prompt] if extract_prompt else [],
        metadata={
            "input_len": len(input_text),
            "rdf_count": len(verified_rdf),
            "lpg_nodes": len(lpg_graph.get("nodes", []))
        }
    )
    
    return {
        "rdf_triples": verified_rdf,
        "lpg_graph": lpg_graph
    }

# ==========================================
# 4. Export Logic
# ==========================================
def save_results_separated(all_results):
    # 1. RDF (.ttl)
    ttl_file = RDF_OUT_DIR / "fibo_graph.ttl"
    ttl_content = [
        "@prefix ex: <http://example.org/kb/> .",
        "@prefix fibo-fbc-fi-fi: <https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/> .",
        "@prefix fibo-be-le-lp: <https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/> .",
        "@prefix fibo-fnd-rel-rel: <https://spec.edmcouncil.org/fibo/ontology/FND/Relations/Relations/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        ""
    ]
    
    lpg_nodes = {}
    lpg_edges = []
    
    for res in all_results:
        # RDF Accumulation
        for t in res.get("rdf_triples", []):
            subj = t['subject'] if ":" in t['subject'] else f"ex:{t['subject']}"
            pred = t['predicate']
            obj = t['object']
            
            # Simple check for URI vs Literal
            if any(x in obj for x in ["http", "ex:", "fibo"]):
                line = f"<{subj}> <{pred}> <{obj}> ."
            else:
                line = f"<{subj}> <{pred}> \"{obj}\" ."
            ttl_content.append(line)
        
        # LPG Accumulation
        graph = res.get("lpg_graph", {})
        for n in graph.get("nodes", []):
            nid = n.get("id")
            if nid:
                lpg_nodes[nid] = {
                    "id": nid, 
                    "label": n.get("label", "Entity"), 
                    "props": json.dumps(n.get("properties", {}))
                }
        for e in graph.get("relationships", []):
            lpg_edges.append({
                "source": e.get("source"),
                "target": e.get("target"),
                "type": e.get("type", "RELATED"),
                "props": json.dumps(e.get("properties", {}))
            })

    # Save RDF
    with open(ttl_file, "w", encoding="utf-8") as f:
        f.write("\n".join(ttl_content))

    # Save LPG CSVs
    with open(LPG_OUT_DIR / "nodes.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "label", "props"])
        writer.writeheader()
        writer.writerows(lpg_nodes.values())
    with open(LPG_OUT_DIR / "edges.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "type", "props"])
        writer.writeheader()
        writer.writerows(lpg_edges)
        
    print(f"\n📦 Export Summary:")
    print(f"   - RDF: {len(ttl_content)} triples -> {ttl_file}")
    print(f"   - LPG: {len(lpg_nodes)} nodes, {len(lpg_edges)} edges -> {LPG_OUT_DIR}")

# ==========================================
# 5. Execution (Batch 1000)
# ==========================================
if __name__ == "__main__":
    setup_prompts()
    
    print("\n🔍 Fetching dataset 'fibo-evaluation-dataset' from Opik...")
    try:
        dataset = OPIK_CLIENT.get_dataset(name="fibo-evaluation-dataset")
        items = dataset.get_content() # SDK에 따라 메서드가 다를 수 있음 (보통 get_content 혹은 리스트 반환)
        print(f"✅ Loaded {len(items)} items from dataset.")
    except Exception as e:
        print(f"❌ Failed to load dataset: {e}")
        # Fallback for testing
        items = []

    # 처음 1000개만 슬라이싱
    target_items = items[:1000]
    print(f"🚀 Processing first {len(target_items)} items...")

    all_outputs = []
    
    # tqdm을 사용하여 진행률 표시
    for item in tqdm(target_items, desc="Pipeline Progress"):
        # Metadata 내부의 references 컬럼 추출
        try:
            # Opik 데이터 구조는 {'metadata': {'references': '...'}, ...} 형태
            metadata = item.get("metadata", {})
            refs = metadata.get("references", "")
            
            if not refs: 
                continue # 비어있으면 스킵

            # 리스트면 합치고 스트링이면 그대로 사용
            text = " ".join([str(r) for r in refs]) if isinstance(refs, list) else str(refs)
            
            # 파이프라인 실행
            result = run_fibo_pipeline(text)
            all_outputs.append(result)
            
        except Exception as e:
            # 개별 아이템 에러가 전체를 멈추지 않도록 처리
            # print(f"Skipping item due to error: {e}") 
            pass
        
    # 결과 저장
    save_results_separated(all_outputs)
    print("\n✅ Batch Processing Completed.")