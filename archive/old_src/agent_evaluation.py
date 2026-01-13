import os
import sys
import argparse
import json
from typing import Set
from opik import Opik, track
from opik.opik_context import update_current_trace
from opik.evaluation import evaluate
from opik.evaluation.metrics import Hallucination, AnswerRelevance, ContextRecall, Usefulness
from agents import Runner
from src.agents.agent_factory import AgentFactory, ToolMode
from src.utils.retrieval_metrics import RetrievalQuality, RetrievalRelevance, DatabaseSelectionQuality
from src.utils.experiment_metrics import RoutingAccuracy, ContextPrecision, ConflictResolutionScore, ToolCallQuality

# ==========================================
# 1. Config
# ==========================================
os.environ["OPIK_URL_OVERRIDE"] = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
os.environ["OPIK_PROJECT_NAME"] = "graph-agent-ablation"

# ==========================================
# 2. Evaluation Task
# ==========================================
def get_evaluation_task(agent):
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

# ==========================================
# 3. Experiment Runner
# ==========================================
def run_ablation_experiment(modes: Set[ToolMode], use_manager: bool = True):
    mode_names = [m.value for m in modes]
    exp_name = f"ablation_{'_'.join(sorted(mode_names))}"
    if use_manager:
        exp_name += "_manager"
    
    print(f"\n🚀 Starting Experiment: {exp_name}")
    
    if use_manager:
        agent = AgentFactory.create_manager_agent(modes)
    else:
        agent = AgentFactory.create_agent(modes)
    
    client = Opik()
    dataset = client.get_dataset(name="fibo-evaluation-dataset")
    
    metrics = [
        AnswerRelevance(), Usefulness(), Hallucination(),
        RetrievalQuality(), RetrievalRelevance(), DatabaseSelectionQuality(),
        RoutingAccuracy(), ContextPrecision(), ConflictResolutionScore(), ToolCallQuality()
    ]
    
    evaluate(
        experiment_name=exp_name,
        dataset=dataset,
        task=get_evaluation_task(agent),
        scoring_metrics=metrics,
        verbose=1
    )

# ==========================================
# 4. Main
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent Ablation Evaluation")
    parser.add_argument("--modes", type=str, help="Comma-separated modes: lpg,rdf,hybrid")
    parser.add_argument("--no-manager", action="store_true", help="Use single agent instead of manager")
    parser.add_argument("--all", action="store_true", help="Run all ablation combinations")
    
    args = parser.parse_args()
    
    if args.all:
        combinations = [
            {ToolMode.LPG},
            {ToolMode.RDF},
            {ToolMode.HYBRID},
            {ToolMode.LPG, ToolMode.RDF},
            {ToolMode.LPG, ToolMode.HYBRID},
            {ToolMode.RDF, ToolMode.HYBRID},
            {ToolMode.LPG, ToolMode.RDF, ToolMode.HYBRID}
        ]
        for combo in combinations:
            run_ablation_experiment(combo, not args.no_manager)
    elif args.modes:
        mode_list = [ToolMode(m.strip().lower()) for m in args.modes.split(",")]
        run_ablation_experiment(set(mode_list), not args.no_manager)
    else:
        print("Example Usage:")
        print("  python agent_evaluation.py --modes lpg,hybrid")
        print("  python agent_evaluation.py --all")