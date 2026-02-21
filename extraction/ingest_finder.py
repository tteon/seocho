import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pandas as pd
import yaml
from openai import OpenAI

from graph_loader import GraphLoader
from tracing import wrap_openai_client
from vector_store import VectorStore

# Initialize generic client, will set API key in main or environment
client = None


_ENV_PATTERN = "${oc.env:"


def _resolve_env_templates(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_env_templates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_templates(v) for v in value]
    if not isinstance(value, str) or _ENV_PATTERN not in value:
        return value

    text = value.strip()
    if not (text.startswith("${oc.env:") and text.endswith("}")):
        return value

    body = text[len("${oc.env:"):-1]
    if "," in body:
        key, default = body.split(",", 1)
    else:
        key, default = body, ""
    return os.getenv(key.strip(), default.strip())


def _to_namespace(payload: Any) -> Any:
    if isinstance(payload, dict):
        return SimpleNamespace(**{key: _to_namespace(value) for key, value in payload.items()})
    if isinstance(payload, list):
        return [_to_namespace(value) for value in payload]
    return payload


def _namespace_to_primitive(payload: Any) -> Any:
    if isinstance(payload, SimpleNamespace):
        return {key: _namespace_to_primitive(value) for key, value in payload.__dict__.items()}
    if isinstance(payload, dict):
        return {key: _namespace_to_primitive(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_namespace_to_primitive(value) for value in payload]
    return payload


def _load_ingestion_config(schema_name: str | None = None):
    base_dir = Path(__file__).resolve().parent / "conf" / "ingestion"
    cfg_path = base_dir / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fp:
        cfg_raw = yaml.safe_load(fp) or {}

    default_schema_name = cfg_raw.get("schema")
    if not default_schema_name:
        defaults = cfg_raw.get("defaults", [])
        for item in defaults:
            if isinstance(item, dict) and "schema" in item:
                default_schema_name = str(item["schema"])
                break

    resolved_schema_name = schema_name or os.getenv("INGEST_SCHEMA", default_schema_name or "baseline")
    schema_path = base_dir / "schema" / f"{resolved_schema_name}.yaml"
    if not schema_path.exists():
        raise FileNotFoundError(f"schema config not found: {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as fp:
        schema_raw = yaml.safe_load(fp) or {}

    merged = {
        "dataset": cfg_raw.get("dataset", {}),
        "neo4j": cfg_raw.get("neo4j", {}),
        "openai": cfg_raw.get("openai", {}),
        "schema": schema_raw,
    }
    merged = _resolve_env_templates(merged)
    return _to_namespace(merged)


def call_openai_for_json(prompt_text, json_schema, model="gpt-4o"):
    """
    Calls OpenAI API with Strict Structured Outputs.
    """
    global client
    try:
        schema_dict = json_schema if isinstance(json_schema, dict) else {}

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant expert in data relationship capture and extraction.",
                },
                {"role": "user", "content": prompt_text},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction_response",
                    "schema": schema_dict,
                    "strict": True,
                },
            },
            temperature=0,
        )
        output_content = response.choices[0].message.content
        return json.loads(output_content)
    except Exception as e:
        print(f"Error calling OpenAI: {e}")
        # Fallback for complex failures
        return {}


def transform_to_graph_format(doc_id, extraction_result, linking_result, schema_name):
    """
    Transforms User Logic (Baseline/FIBO) to GraphLoader format.
    GraphLoader expects: { "nodes": [{"id":..., "label":..., "properties":...}], "relationships": [...] }
    """
    nodes = []
    relationships = []

    extracted = extraction_result.get("extracted_entities", [])
    for ent in extracted:
        # Baseline uses 'type', FIBO uses 'fibo_class'
        label = ent.get("type") or ent.get("fibo_class") or "Unknown"
        text = ent["text"]

        nodes.append(
            {
                "id": text,
                "label": label,
                "properties": {
                    "name": text,
                    "source_doc": str(doc_id),
                    "schema_mode": schema_name,
                },
            }
        )

    rels = linking_result.get("entity_relationships", [])
    for rel in rels:
        relationships.append(
            {
                "source": rel["source_entity"],
                "target": rel["target_entity"],
                "type": rel["relation_type"],
                "properties": {
                    "source_doc": str(doc_id),
                    "schema_mode": schema_name,
                },
            }
        )

    return {"nodes": nodes, "relationships": relationships}


def main(schema_name: str | None = None):
    cfg = _load_ingestion_config(schema_name=schema_name)
    print(f"Starting ingestion with schema: {cfg.schema.name}")

    global client
    client = wrap_openai_client(OpenAI(api_key=os.getenv("OPENAI_API_KEY")))

    # 1. Load Dataset
    print(f"Loading dataset from {cfg.dataset.path}...")
    try:
        df = pd.read_parquet(cfg.dataset.path)
    except Exception as e:
        print(f"Failed to load dataset: {e}")
        return

    # 2. Filter & Sample
    target_categories = cfg.dataset.categories
    sample_size = int(cfg.dataset.sample_size)

    sampled_dfs = []
    for cat in target_categories:
        cat_df = df[df["category"] == cat]
        if not cat_df.empty:
            print(f"Sampling {sample_size} from {cat} (Total: {len(cat_df)})")
            sampled_dfs.append(cat_df.sample(n=min(sample_size, len(cat_df)), random_state=42))

    if not sampled_dfs:
        print("No data found for categories.")
        return

    sample_df = pd.concat(sampled_dfs).reset_index(drop=True)
    print(f"Total records to process: {len(sample_df)}")

    # 3. Initialize Connections
    # Check for override in schema config (e.g., for 'kgfibo')
    uri = getattr(cfg.schema, "neo4j_uri_override", cfg.neo4j.uri)
    print(f"Connecting to Neo4j at {uri}...")

    graph_loader = GraphLoader(uri, cfg.neo4j.user, cfg.neo4j.password)
    vector_store = VectorStore(api_key=os.getenv("OPENAI_API_KEY"))

    # Output for vectors is shared or could be split?
    # For now shared output folder, but we could make it specific.
    output_dir = "output"

    # 4. Processing Loop
    for idx, row in sample_df.iterrows():
        text = row["references"]
        if isinstance(text, list):
            text = "\n".join(text)

        doc_id = str(row.get("_id", f"doc_{idx}"))
        print(f"[{idx+1}/{len(sample_df)}] Processing {doc_id}...")

        # A. Extraction
        ex_prompt_tmpl = cfg.schema.prompts.extraction
        ex_prompt = ex_prompt_tmpl.format(input_text=text[:4000])

        ex_res = call_openai_for_json(
            ex_prompt,
            _namespace_to_primitive(cfg.schema.schemas.extraction),
            model=cfg.openai.model,
        )

        # B. Linking
        link_res: Dict[str, Any] = {}
        ents = ex_res.get("extracted_entities", [])
        if len(ents) > 1:
            link_prompt_tmpl = cfg.schema.prompts.linking
            # Dump entities to JSON string for prompt injection
            ents_json = json.dumps(ents, ensure_ascii=False, indent=2)
            link_prompt = link_prompt_tmpl.format(extracted_entities=ents_json, input_text=text[:4000])

            link_res = call_openai_for_json(
                link_prompt,
                _namespace_to_primitive(cfg.schema.schemas.linking),
                model=cfg.openai.model,
            )
        else:
            print("  Skipping linking (not enough entities).")

        # C. Load Graph
        graph_data = transform_to_graph_format(doc_id, ex_res, link_res, cfg.schema.name)
        graph_loader.load_graph(graph_data, source_id=doc_id)

        # D. Load Vector
        vector_store.add_document(doc_id, text)

    # 5. Save & Close
    vector_store.save_index(output_dir)
    graph_loader.close()
    print("Ingestion Batch Complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest FinDER-style dataset to graph and vector stores.")
    parser.add_argument("--schema", default=None, help="Schema name under conf/ingestion/schema (e.g., baseline, fibo)")
    args = parser.parse_args()
    main(schema_name=args.schema)
