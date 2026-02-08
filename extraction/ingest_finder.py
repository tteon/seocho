import pandas as pd
import os
import json
import hydra
from omegaconf import DictConfig, OmegaConf
from openai import OpenAI
from graph_loader import GraphLoader
from vector_store import VectorStore
from tracing import wrap_openai_client

# Initialize generic client, will set API key in main or environment
client = None

def call_openai_for_json(prompt_text, json_schema, model="gpt-4o"):
    """
    Calls OpenAI API with Strict Structured Outputs.
    """
    global client
    try:
        # OmegaConf's DictConfig isn't directly serializable to JSON schema sometimes,
        # convert to primitive python dict
        schema_dict = OmegaConf.to_container(json_schema, resolve=True)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant expert in data relationship capture and extraction."},
                {"role": "user", "content": prompt_text}
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction_response",
                    "schema": schema_dict,
                    "strict": True
                }
            },
            temperature=0
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
    
    extracted = extraction_result.get('extracted_entities', [])
    for ent in extracted:
        # Baseline uses 'type', FIBO uses 'fibo_class'
        label = ent.get('type') or ent.get('fibo_class') or "Unknown"
        text = ent['text']
        
        nodes.append({
            "id": text, 
            "label": label,
            "properties": {
                "name": text, 
                "source_doc": str(doc_id),
                "schema_mode": schema_name
            }
        })
        
    rels = linking_result.get('entity_relationships', [])
    for rel in rels:
        relationships.append({
            "source": rel['source_entity'],
            "target": rel['target_entity'],
            "type": rel['relation_type'],
            "properties": {
                "source_doc": str(doc_id),
                "schema_mode": schema_name
            }
        })
        
    return {"nodes": nodes, "relationships": relationships}

@hydra.main(version_base=None, config_path="conf/ingestion", config_name="config")
def main(cfg: DictConfig):
    print(f"Starting ingestion with schema: {cfg.schema.name}")
    print(f"Configuration:\n{OmegaConf.to_yaml(cfg)}")
    
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
    sample_size = cfg.dataset.sample_size
    
    sampled_dfs = []
    for cat in target_categories:
        cat_df = df[df['category'] == cat]
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
    uri = cfg.schema.get("neo4j_uri_override", cfg.neo4j.uri)
    print(f"Connecting to Neo4j at {uri}...")
    
    graph_loader = GraphLoader(uri, cfg.neo4j.user, cfg.neo4j.password)
    vector_store = VectorStore(api_key=os.getenv("OPENAI_API_KEY"))
    
    # Output for vectors is shared or could be split? 
    # For now shared output folder, but we could make it specific.
    output_dir = "output" 
    
    # 4. Processing Loop
    for idx, row in sample_df.iterrows():
        text = row['references']
        if isinstance(text, list): text = "\n".join(text)
        
        doc_id = str(row.get('_id', f"doc_{idx}"))
        print(f"[{idx+1}/{len(sample_df)}] Processing {doc_id}...")
        
        # A. Extraction
        # Hydra loads multiline strings, passed to prompt
        ex_prompt_tmpl = cfg.schema.prompts.extraction
        ex_prompt = ex_prompt_tmpl.format(input_text=text[:4000])
        
        ex_res = call_openai_for_json(ex_prompt, cfg.schema.schemas.extraction, model=cfg.openai.model)
        
        # B. Linking
        link_res = {}
        ents = ex_res.get('extracted_entities', [])
        if len(ents) > 1:
            link_prompt_tmpl = cfg.schema.prompts.linking
            # Dump entities to JSON string for prompt injection
            ents_json = json.dumps(ents, ensure_ascii=False, indent=2)
            link_prompt = link_prompt_tmpl.format(extracted_entities=ents_json, input_text=text[:4000])
            
            link_res = call_openai_for_json(link_prompt, cfg.schema.schemas.linking, model=cfg.openai.model)
        else:
            print("  Skipping linking (not enough entities).")
            
        # C. Load Graph
        graph_data = transform_to_graph_format(doc_id, ex_res, link_res, cfg.schema.name)
        graph_loader.load_graph(graph_data, source_id=doc_id)
        
        # D. Load Vector
        # We might want to tag vector docs with schema mode too?
        vector_store.add_document(doc_id, text)

    # 5. Save & Close
    vector_store.save_index(output_dir)
    graph_loader.close()
    print("Ingestion Batch Complete.")

if __name__ == "__main__":
    main()
