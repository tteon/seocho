"""
CLI: Export
Export data from Opik.

Usage:
    python -m src.cli.export --traces           # Export traces
    python -m src.cli.export --datasets         # Export all datasets
    python -m src.cli.export --dataset NAME     # Export specific dataset
"""
import argparse
from src.data.opik_utils import export_traces, export_datasets


def main():
    parser = argparse.ArgumentParser(description="Export data from Opik")
    parser.add_argument("--traces", action="store_true", help="Export project traces")
    parser.add_argument("--datasets", action="store_true", help="Export all datasets")
    parser.add_argument("--dataset", type=str, help="Export specific dataset by name")
    parser.add_argument("--project", type=str, default="kgbuild", help="Project name")
    parser.add_argument("--output", type=str, help="Output directory")
    
    args = parser.parse_args()
    
    if args.traces:
        print("🚀 Exporting traces...")
        export_traces(project_name=args.project, output_dir=args.output)
    elif args.datasets:
        print("🚀 Exporting all datasets...")
        export_datasets(output_dir=args.output)
    elif args.dataset:
        print(f"🚀 Exporting dataset: {args.dataset}")
        export_datasets(output_dir=args.output, dataset_names=[args.dataset])
    else:
        print("Usage:")
        print("  python -m src.cli.export --traces")
        print("  python -m src.cli.export --datasets")
        print("  python -m src.cli.export --dataset fibo-evaluation-dataset")


if __name__ == "__main__":
    main()
