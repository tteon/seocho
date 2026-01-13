#!/usr/bin/env python3
"""
Export all Opik datasets to JSON format

This script:
1. Connects to Opik and lists all datasets in a workspace
2. Exports each dataset with all its items
3. Handles pagination for large datasets
4. Creates a JSON file per dataset
"""

import os
import json
import sys
from pathlib import Path
from typing import List, Dict, Any
import opik
from opik import Opik
from tqdm import tqdm

# Configuration
OPIK_URL_OVERRIDE = os.getenv("OPIK_URL_OVERRIDE", "http://opik-backend-1:8080")
OPIK_WORKSPACE = os.getenv("OPIK_WORKSPACE", "default")
OUTPUT_DIR = Path("/workspace/output/opik_exports")

# Set environment variables
os.environ["OPIK_URL_OVERRIDE"] = OPIK_URL_OVERRIDE
if OPIK_WORKSPACE:
    os.environ["OPIK_WORKSPACE"] = OPIK_WORKSPACE


def export_all_datasets():
    """Export all datasets from Opik to JSON files."""

    # Initialize Opik client
    print("=" * 70)
    print("Opik Dataset Exporter")
    print(f"URL: {OPIK_URL_OVERRIDE}")
    print(f"Workspace: {OPIK_WORKSPACE}")
    print("=" * 70)

    client = Opik()
    print("✓ Connected to Opik")

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"✓ Output directory: {OUTPUT_DIR}")

    # Get all datasets (with pagination handling)
    print("\nFetching all datasets...")
    datasets = client.get_datasets(max_results=1000)

    if not datasets:
        print("✗ No datasets found")
        return

    print(f"✓ Found {len(datasets)} datasets")

    # Export each dataset
    total_items = 0
    for dataset in datasets:
        dataset_name = dataset.name
        print(f"\n{'=' * 70}")
        print(f"Exporting dataset: {dataset_name}")
        print(f"{'=' * 70}")

        # Get all items in the dataset
        print(f"Fetching items from dataset '{dataset_name}'...")
        items = dataset.get_items()  # This handles pagination internally

        if not items:
            print(f"  ⚠ No items in dataset '{dataset_name}'")
            continue

        print(f"✓ Retrieved {len(items)} items")
        total_items += len(items)

        # Prepare data for export
        export_data = {
            "dataset_name": dataset_name,
            "dataset_description": dataset.description,
            "workspace": OPIK_WORKSPACE,
            "total_items": len(items),
            "items": items
        }

        # Save to JSON file
        output_file = OUTPUT_DIR / f"{dataset_name}_export.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        print(f"✓ Saved to: {output_file}")

        # Show sample of the data
        if items:
            print(f"\nSample item structure:")
            sample = items[0]
            print(f"  Keys: {list(sample.keys())}")
            if 'input' in sample:
                input_preview = str(sample['input'])[:100]
                print(f"  Input preview: {input_preview}...")
            if 'expected_output' in sample:
                output_preview = str(sample['expected_output'])[:100]
                print(f"  Expected output preview: {output_preview}...")

    # Summary
    print(f"\n{'=' * 70}")
    print("Export Complete!")
    print(f"{'=' * 70}")
    print(f"Total datasets: {len(datasets)}")
    print(f"Total items exported: {total_items}")
    print(f"Output directory: {OUTPUT_DIR}")

    # List exported files
    print(f"\nExported files:")
    for f in sorted(OUTPUT_DIR.glob("*.json")):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"  - {f.name} ({size_mb:.2f} MB)")


def export_specific_dataset(dataset_name: str):
    """Export a specific dataset by name."""

    print("=" * 70)
    print(f"Exporting specific dataset: {dataset_name}")
    print("=" * 70)

    client = Opik()

    try:
        dataset = client.get_dataset(name=dataset_name)
        print(f"✓ Found dataset: {dataset.name}")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        print(f"\nFetching items...")
        items = dataset.get_items()

        if not items:
            print(f"⚠ No items found in dataset '{dataset_name}'")
            return

        print(f"✓ Retrieved {len(items)} items")

        # Save to JSON
        output_file = OUTPUT_DIR / f"{dataset_name}_export.json"
        export_data = {
            "dataset_name": dataset_name,
            "dataset_description": dataset.description,
            "workspace": OPIK_WORKSPACE,
            "total_items": len(items),
            "items": items
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        print(f"✓ Saved to: {output_file}")
        print(f"File size: {output_file.stat().st_size / (1024*1024):.2f} MB")

    except Exception as e:
        print(f"✗ Error accessing dataset '{dataset_name}': {e}")


def main():
    """Main entry point."""

    # Check if specific dataset name is provided as argument
    if len(sys.argv) > 1:
        dataset_name = sys.argv[1]
        export_specific_dataset(dataset_name)
    else:
        export_all_datasets()


if __name__ == "__main__":
    main()
