"""
Opik Utilities
Consolidated export and dataset operations.
Merged from export.py and export_opik_datasets.py
"""
import os
import json
import csv
from pathlib import Path
from typing import List, Dict, Any, Optional
from opik import Opik
from tqdm import tqdm

from src.config.settings import OPIK_URL, EXPORT_OPIK_DIR


# Set environment
os.environ["OPIK_URL_OVERRIDE"] = OPIK_URL


def export_traces(
    project_name: str = "kgbuild",
    track_name: str = "fibo-main-pipeline",
    output_dir: str = None,
    max_results: int = 10000
) -> Path:
    """
    Export project traces to JSON and CSV.
    
    Args:
        project_name: Opik project name
        track_name: Optional filter for specific pipeline traces
        output_dir: Output directory path
        max_results: Maximum traces to export
    
    Returns:
        Path to the exported JSON file
    """
    output_dir = Path(output_dir or EXPORT_OPIK_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    client = Opik(project_name=project_name)
    
    print(f"🔍 Fetching traces from project: {project_name}...")
    traces = client.search_traces(project_name=project_name, max_results=max_results)
    
    # Filter by track name if specified
    if track_name:
        traces = [t for t in traces if t.name == track_name]
    
    print(f"✅ Found {len(traces)} traces.")
    
    export_data = []
    for trace in tqdm(traces, desc="Formatting Data"):
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
    
    # Save JSON
    json_path = output_dir / f"{project_name}_export.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)
    
    # Save CSV
    csv_path = output_dir / f"{project_name}_export.csv"
    if export_data:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=export_data[0].keys())
            writer.writeheader()
            writer.writerows(export_data)
    
    print(f"📂 Export Complete:")
    print(f"   - JSON: {json_path}")
    print(f"   - CSV: {csv_path}")
    
    return json_path


def export_datasets(
    output_dir: str = None,
    workspace: str = "default",
    dataset_names: List[str] = None
) -> List[Path]:
    """
    Export Opik datasets to JSON files.
    
    Args:
        output_dir: Output directory path
        workspace: Opik workspace name
        dataset_names: Specific datasets to export (None for all)
    
    Returns:
        List of exported file paths
    """
    output_dir = Path(output_dir or EXPORT_OPIK_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    client = Opik()
    exported_files = []
    
    if dataset_names:
        # Export specific datasets
        for name in dataset_names:
            try:
                dataset = client.get_dataset(name=name)
                path = _export_single_dataset(dataset, output_dir, workspace)
                exported_files.append(path)
            except Exception as e:
                print(f"⚠️ Error exporting {name}: {e}")
    else:
        # Export all datasets
        datasets = client.get_datasets(max_results=1000)
        for dataset in datasets:
            try:
                path = _export_single_dataset(dataset, output_dir, workspace)
                exported_files.append(path)
            except Exception as e:
                print(f"⚠️ Error exporting {dataset.name}: {e}")
    
    print(f"\n✅ Exported {len(exported_files)} datasets to {output_dir}")
    return exported_files


def _export_single_dataset(dataset, output_dir: Path, workspace: str) -> Path:
    """Export a single dataset to JSON."""
    items = dataset.get_items()
    
    export_data = {
        "dataset_name": dataset.name,
        "dataset_description": dataset.description,
        "workspace": workspace,
        "total_items": len(items),
        "items": items
    }
    
    output_file = output_dir / f"{dataset.name}_export.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Exported {dataset.name}: {len(items)} items")
    return output_file


def create_dataset_subset(
    source_name: str,
    target_name: str,
    sample_size: int = 50
) -> bool:
    """
    Create a subset of an existing dataset.
    
    Args:
        source_name: Source dataset name
        target_name: Target dataset name
        sample_size: Number of items to include
    
    Returns:
        Success status
    """
    client = Opik()
    
    try:
        source = client.get_dataset(name=source_name)
        items = source.get_items()
        
        subset_items = items[:sample_size]
        target = client.get_or_create_dataset(name=target_name)
        target.insert(subset_items)
        
        print(f"✅ Created subset '{target_name}' with {len(subset_items)} items")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
