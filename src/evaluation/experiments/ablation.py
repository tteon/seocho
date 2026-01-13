"""
Ablation Study Configurations
Defines all combinations for systematic component analysis.
"""
from enum import Enum
from typing import Set, Dict, Any


class ToolMode(Enum):
    """Available retrieval modes."""
    LPG = "lpg"
    RDF = "rdf"
    HYBRID = "hybrid"


# All ablation combinations
ABLATION_COMBINATIONS = [
    # Single retrieval methods
    {"id": "A1", "name": "LPG Only", "modes": {ToolMode.LPG}},
    {"id": "A2", "name": "RDF Only", "modes": {ToolMode.RDF}},
    {"id": "A3", "name": "HYBRID Only", "modes": {ToolMode.HYBRID}},
    
    # Pair combinations
    {"id": "A4", "name": "LPG+RDF", "modes": {ToolMode.LPG, ToolMode.RDF}},
    {"id": "A5", "name": "LPG+HYBRID", "modes": {ToolMode.LPG, ToolMode.HYBRID}},
    {"id": "A6", "name": "RDF+HYBRID", "modes": {ToolMode.RDF, ToolMode.HYBRID}},
]


def get_ablation_experiment(experiment_id: str) -> Dict[str, Any]:
    """Get a specific ablation experiment by ID."""
    for exp in ABLATION_COMBINATIONS:
        if exp["id"] == experiment_id:
            return exp
    return None


def get_all_mode_sets():
    """Get all mode sets for ablation."""
    return [exp["modes"] for exp in ABLATION_COMBINATIONS]
