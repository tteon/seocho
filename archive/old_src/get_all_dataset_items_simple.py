#!/usr/bin/env python3
"""
Simple script to fetch all items from an Opik dataset.
Tests different methods to bypass the 100-item limit.
"""

import sys
from pathlib import Path

# Add opik to path
sys.path.insert(0, str(Path("/workspace/opik/sdks/python")))

import opik
from opik import Opik


def test_methods():
    """Test different methods to get all dataset items."""

    DATASET_NAME = "fibo-evaluation-dataset"

    print("=" * 70)
    print(f"Testing methods to fetch all items from: {DATASET_NAME}")
    print("=" * 70)

    # Initialize Opik client
    print("\n1. Initializing Opik client...")
    client = Opik()

    # Get dataset
    print("2. Getting dataset object...")
    dataset = client.get_dataset(name=DATASET_NAME)
    print(f"   Dataset name: {dataset.name}")
    print(f"   Dataset ID: {dataset.id}")

    # Method 1: Use get_items() without parameters
    print("\n3. Method 1: dataset.get_items() [should get all items]")
    try:
        items = dataset.get_items()
        print(f"   ✓ Retrieved {len(items)} items")
        if items:
            print(f"   First item keys: {list(items[0].keys())}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Method 2: Use to_pandas()
    print("\n4. Method 2: dataset.to_pandas()")
    try:
        import pandas as pd
        df = dataset.to_pandas()
        print(f"   ✓ Retrieved {len(df)} rows as DataFrame")
        print(f"   Columns: {list(df.columns)}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    # Method 3: Use internal method
    print("\n5. Method 3: Direct internal API access")
    try:
        # Access the internal streaming method
        internal_items = dataset._Dataset__internal_api__get_items_as_dataclasses__(
            nb_samples=None
        )
        print(f"   ✓ Retrieved {len(internal_items)} items via internal API")

        # Convert to dicts
        dict_items = []
        for item in internal_items:
            item_dict = {"id": item.id, **item.get_content()}
            dict_items.append(item_dict)

        print(f"   ✓ Converted to {len(dict_items)} dictionary items")

        if dict_items:
            print(f"   Sample item: {dict_items[0]}")

    except Exception as e:
        print(f"   ✗ Error: {e}")
        import traceback
        traceback.print_exc()

    # Verify the dataset actually has more than 100 items
    print("\n6. Verification:")
    print(f"   Dataset should have 5103 items according to user")
    print(f"   If we got less than 100, there's a pagination issue")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    test_methods()
