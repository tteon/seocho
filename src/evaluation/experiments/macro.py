"""
Macro Experiment Configurations
Defines system-level comparison experiments.
"""
from typing import Dict, Any, List
from src.evaluation.experiments.ablation import ToolMode


# Macro experiments (system-level comparisons)
MACRO_EXPERIMENTS = [
    {
        "id": "M1",
        "name": "Full System (Manager)",
        "modes": {ToolMode.LPG, ToolMode.RDF, ToolMode.HYBRID},
        "use_manager": True,
        "description": "Full system with orchestrator agent"
    },
    {
        "id": "M2",
        "name": "Full System (Single)",
        "modes": {ToolMode.LPG, ToolMode.RDF, ToolMode.HYBRID},
        "use_manager": False,
        "description": "Full system with single unified agent"
    },
    {
        "id": "M3",
        "name": "LPG+HYBRID (Manager)",
        "modes": {ToolMode.LPG, ToolMode.HYBRID},
        "use_manager": True,
        "description": "Graph facts + text search, no ontology"
    },
    {
        "id": "M4",
        "name": "RDF+HYBRID (Manager)",
        "modes": {ToolMode.RDF, ToolMode.HYBRID},
        "use_manager": True,
        "description": "Ontology + text search, no structured facts"
    },
]


def get_macro_experiment(experiment_id: str) -> Dict[str, Any]:
    """Get a specific macro experiment by ID."""
    for exp in MACRO_EXPERIMENTS:
        if exp["id"] == experiment_id:
            return exp
    return None


def get_all_macro_experiments() -> List[Dict[str, Any]]:
    """Get all macro experiments."""
    return MACRO_EXPERIMENTS
