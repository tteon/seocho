import os
import json
import csv
from pathlib import Path
from opik import Opik
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

# Configuration
PROJECT_NAME = "kgbuild"
TRACK_NAME = "fibo-main-pipeline"
EXPORT_DIR = Path(os.getenv("EXPORT_OPIK_DIR", "/workspace/export_opik"))
EXPORT_DIR.mkdir(exist_ok=True)

client = Opik(project_name=PROJECT_NAME)

def export_project_traces():
    print(f"🔍 Fetching traces from project: {PROJECT_NAME}...")
    
    # Increase limit to capture all historical data (e.g., 2000)
    # Filter by project and track name
    traces = client.search_traces(project_name=PROJECT_NAME, max_results=10000)
    # Filter only for the main pipeline traces
    pipeline_traces = [t for t in traces if t.name == TRACK_NAME]
    
    print(f"✅ Found {len(pipeline_traces)} pipeline traces.")
    
    export_data = []
    
    for trace in tqdm(pipeline_traces, desc="Formatting Data"):
        # Opik traces store data in .input and .output attributes
        # Based on your previous code, input is {'input_text': ...}
        # and output is {'rdf_triples': [...], 'lpg_graph': {...}}
        
        # Handle cases where input/output might be strings or None
        input_data = trace.input if isinstance(trace.input, dict) else {}
        output_data = trace.output if isinstance(trace.output, dict) else {}
        
        entry = {
            "trace_id": trace.id,
            "start_time": trace.start_time.isoformat() if trace.start_time else None,
            "input_text": input_data.get("input_text", str(trace.input)) if trace.input else "",
            "rdf_triples": json.dumps(output_data.get("rdf_triples", [])) if output_data else "[]",
            "lpg_nodes": json.dumps(output_data.get("lpg_graph", {}).get("nodes", [])) if output_data else "[]",
            "lpg_edges": json.dumps(output_data.get("lpg_graph", {}).get("relationships", [])) if output_data else "[]"
        }
        export_data.append(entry)

    # --- SAVE TO JSON ---
    json_path = EXPORT_DIR / f"{PROJECT_NAME}_export.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)
    
    # --- SAVE TO CSV ---
    csv_path = EXPORT_DIR / f"{PROJECT_NAME}_export.csv"
    if export_data:
        keys = export_data[0].keys()
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(export_data)

    print(f"\n📂 Export Complete:")
    print(f"   - JSON: {json_path}")
    print(f"   - CSV: {csv_path}")

if __name__ == "__main__":
    export_project_traces()