"""
Evaluation Runner
Main entry point for running experiments.
Refactored from agent_evaluation.py
"""
import os
import argparse
from typing import Set

from opik import Opik, track
from opik.opik_context import update_current_trace
from opik.evaluation import evaluate
from opik.evaluation.metrics import Hallucination, AnswerRelevance, ContextRecall, Usefulness
from agents import Runner

from src.config.settings import OPIK_URL, OPIK_PROJECT
from src.evaluation.metrics import (
    RetrievalQuality, RetrievalRelevance, DatabaseSelectionQuality,
    RoutingAccuracy, ContextPrecision, ConflictResolutionScore, ToolCallQuality
)
from src.evaluation.experiments.ablation import ToolMode, ABLATION_COMBINATIONS
from src.evaluation.experiments.macro import MACRO_EXPERIMENTS

# Set environment
os.environ["OPIK_URL_OVERRIDE"] = OPIK_URL
os.environ["OPIK_PROJECT_NAME"] = "graph-agent-ablation"


def get_evaluation_task(agent):
    """Create evaluation task for a specific agent."""
    
    @track(name="agent_evaluation_task")
    def evaluation_task(dataset_item):
        raw_input = dataset_item.get("input", {})
        if isinstance(raw_input, dict):
            user_input = raw_input.get("text", str(raw_input))
        else:
            user_input = str(raw_input)
        
        expected_output = dataset_item.get("expected_output", "")
        
        tool_calls = []
        retrieved_context = []
        agent_steps = []
        
        try:
            result = Runner.run_sync(agent, user_input)
            actual_output = result.final_output if result.final_output else ""
            
            for idx, step in enumerate(result.raw_responses):
                step_info = {"step_index": idx}
                if hasattr(step, 'output') and step.output:
                    for item in step.output:
                        if hasattr(item, 'tool_calls') and item.tool_calls:
                            for tc in item.tool_calls:
                                tool_call_info = {
                                    "tool_name": tc.function.name if hasattr(tc, 'function') else str(tc),
                                    "arguments": tc.function.arguments if hasattr(tc, 'function') else ""
                                }
                                tool_calls.append(tool_call_info)
                                step_info["tool_call"] = tool_call_info
                        
                        if hasattr(item, 'content') and item.content:
                            retrieved_context.append(str(item.content))
                agent_steps.append(step_info)
            
            update_current_trace(
                metadata={
                    "agent_name": agent.name,
                    "num_tool_calls": len(tool_calls),
                    "tools_used": list(set([tc["tool_name"] for tc in tool_calls]))
                }
            )
            
        except Exception as e:
            actual_output = f"Agent Failed: {str(e)}"
            update_current_trace(metadata={"error": str(e)})

        return {
            "input": user_input,
            "output": actual_output,
            "reference": expected_output,
            "tool_calls": tool_calls,
            "agent_steps": agent_steps,
            "retrieved_context": "\n".join(retrieved_context) if retrieved_context else "",
            "context": dataset_item.get("metadata", {}).get("references", [])
        }
    
    return evaluation_task


def get_all_metrics():
    """Get all evaluation metrics."""
    return [
        # Standard LLM metrics
        AnswerRelevance(),
        Usefulness(),
        Hallucination(),
        # Custom retrieval metrics
        RetrievalQuality(),
        RetrievalRelevance(),
        DatabaseSelectionQuality(),
        # Experiment metrics
        RoutingAccuracy(),
        ContextPrecision(),
        ConflictResolutionScore(),
        ToolCallQuality()
    ]


def run_experiment(
    modes: Set[ToolMode],
    use_manager: bool = True,
    dataset_name: str = "fibo-evaluation-dataset",
    experiment_name: str = None
):
    """
    Run a single experiment with specified configuration.
    
    Args:
        modes: Set of ToolMode values to use
        use_manager: Whether to use manager agent architecture
        dataset_name: Name of the evaluation dataset
        experiment_name: Optional custom experiment name
    """
    # Import here to avoid circular imports
    from src.agents.agent_factory import AgentFactory
    
    # Generate experiment name
    mode_names = [m.value for m in modes]
    if experiment_name is None:
        experiment_name = f"ablation_{'_'.join(sorted(mode_names))}"
        if use_manager:
            experiment_name += "_manager"
    
    print(f"\n🚀 Starting Experiment: {experiment_name}")
    
    # Create agent
    if use_manager:
        agent = AgentFactory.create_manager_agent(modes)
    else:
        agent = AgentFactory.create_agent(modes)
    
    # Get dataset
    client = Opik()
    dataset = client.get_dataset(name=dataset_name)
    
    # Run evaluation
    evaluate(
        experiment_name=experiment_name,
        dataset=dataset,
        task=get_evaluation_task(agent),
        scoring_metrics=get_all_metrics(),
        verbose=1
    )


def run_ablation_study(use_manager: bool = True):
    """Run all ablation experiments."""
    print("=" * 70)
    print("🔬 Running Full Ablation Study")
    print("=" * 70)
    
    for exp in ABLATION_COMBINATIONS:
        print(f"\n📊 Experiment {exp['id']}: {exp['name']}")
        run_experiment(exp["modes"], use_manager=use_manager)


def run_macro_experiments():
    """Run all macro experiments."""
    print("=" * 70)
    print("🔬 Running Macro Experiments")
    print("=" * 70)
    
    for exp in MACRO_EXPERIMENTS:
        print(f"\n📊 Experiment {exp['id']}: {exp['name']}")
        run_experiment(
            exp["modes"],
            use_manager=exp["use_manager"],
            experiment_name=f"macro_{exp['id'].lower()}"
        )


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Agent Evaluation Runner")
    parser.add_argument("--modes", type=str, help="Comma-separated modes: lpg,rdf,hybrid")
    parser.add_argument("--no-manager", action="store_true", help="Use single agent instead of manager")
    parser.add_argument("--ablation", action="store_true", help="Run full ablation study")
    parser.add_argument("--macro", action="store_true", help="Run macro experiments")
    parser.add_argument("--all", action="store_true", help="Run all experiments")
    
    args = parser.parse_args()
    
    if args.all:
        run_macro_experiments()
        run_ablation_study(use_manager=True)
    elif args.macro:
        run_macro_experiments()
    elif args.ablation:
        run_ablation_study(use_manager=not args.no_manager)
    elif args.modes:
        mode_list = [ToolMode(m.strip().lower()) for m in args.modes.split(",")]
        run_experiment(set(mode_list), not args.no_manager)
    else:
        print("Usage:")
        print("  python -m src.evaluation.runner --modes lpg,hybrid")
        print("  python -m src.evaluation.runner --ablation")
        print("  python -m src.evaluation.runner --macro")
        print("  python -m src.evaluation.runner --all")


if __name__ == "__main__":
    main()
