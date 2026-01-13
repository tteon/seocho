"""
CLI: Evaluate
Run agent evaluation experiments.

Usage:
    python -m src.cli.evaluate --modes lpg,hybrid
    python -m src.cli.evaluate --ablation
    python -m src.cli.evaluate --macro
    python -m src.cli.evaluate --all
"""
import argparse
from src.evaluation.runner import (
    run_experiment, run_ablation_study, run_macro_experiments
)
from src.evaluation.experiments.ablation import ToolMode


def main():
    parser = argparse.ArgumentParser(description="Run agent evaluation experiments")
    parser.add_argument("--modes", type=str, help="Comma-separated modes: lpg,rdf,hybrid")
    parser.add_argument("--no-manager", action="store_true", help="Use single agent")
    parser.add_argument("--ablation", action="store_true", help="Run full ablation study")
    parser.add_argument("--macro", action="store_true", help="Run macro experiments")
    parser.add_argument("--all", action="store_true", help="Run all experiments")
    parser.add_argument("--dataset", type=str, default="fibo-evaluation-dataset",
                        help="Evaluation dataset name")
    
    args = parser.parse_args()
    
    if args.all:
        print("🚀 Running all experiments...")
        run_macro_experiments()
        run_ablation_study(use_manager=True)
    elif args.macro:
        print("🚀 Running macro experiments...")
        run_macro_experiments()
    elif args.ablation:
        print("🚀 Running ablation study...")
        run_ablation_study(use_manager=not args.no_manager)
    elif args.modes:
        mode_list = [ToolMode(m.strip().lower()) for m in args.modes.split(",")]
        run_experiment(set(mode_list), not args.no_manager, args.dataset)
    else:
        print("Usage:")
        print("  python -m src.cli.evaluate --modes lpg,hybrid")
        print("  python -m src.cli.evaluate --ablation")
        print("  python -m src.cli.evaluate --macro")
        print("  python -m src.cli.evaluate --all")


if __name__ == "__main__":
    main()
