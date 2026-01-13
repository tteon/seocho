# Contributing to Graph RAG Framework

This guide explains how to extend the framework with new components.

---

## 📂 Module Overview

| Module | Purpose | Key Files |
|--------|---------|-----------|
| `config/` | Centralized settings | `settings.py`, `schemas.py` |
| `retrieval/` | Database tools | `lpg_tools.py`, `rdf_tools.py`, `lancedb_tools.py` |
| `indexing/` | Data ingestion | `lancedb_indexer.py`, `neo4j_indexer.py` |
| `evaluation/` | Experiments | `runner.py`, `metrics/`, `experiments/` |
| `agents/` | Agent definitions | `agent_factory.py` |

---

## 🔧 Adding a New Retrieval Tool

### 1. Create the tool in `src/retrieval/`

```python
# src/retrieval/my_tools.py
from opik import track
from agents import function_tool

@track(name="my_custom_search")
def _my_search_impl(query: str) -> str:
    """Internal implementation with tracing."""
    # Your search logic here
    return results

@function_tool
def my_search(query: str) -> str:
    """
    Description shown to the agent.
    """
    return _my_search_impl(query)
```

### 2. Export in `__init__.py`

```python
# src/retrieval/__init__.py
from src.retrieval.my_tools import my_search
```

### 3. Add to AgentFactory

```python
# src/agents/agent_factory.py
from src.retrieval.my_tools import my_search

# In create_agent():
if ToolMode.MY_MODE in modes:
    tools.append(my_search)
```

---

## 📊 Adding a New Metric

### 1. Create the metric in `src/evaluation/metrics/`

```python
# src/evaluation/metrics/custom.py
from opik.evaluation.metrics import base_metric, score_result

class MyCustomMetric(base_metric.BaseMetric):
    def __init__(self, name: str = "my_metric"):
        self.name = name
    
    def score(self, input, output, **kwargs):
        # Your scoring logic
        score_value = 0.5
        
        return score_result.ScoreResult(
            name=self.name,
            value=score_value,
            reason="Explanation of score"
        )
```

### 2. Register in runner.py

```python
# src/evaluation/runner.py
from src.evaluation.metrics.custom import MyCustomMetric

def get_all_metrics():
    return [
        # ... existing metrics
        MyCustomMetric(),
    ]
```

---

## 🧪 Adding a New Experiment

### 1. Define in `src/evaluation/experiments/`

```python
# src/evaluation/experiments/custom.py
from src.evaluation.experiments.ablation import ToolMode

MY_EXPERIMENTS = [
    {
        "id": "X1",
        "name": "My Experiment",
        "modes": {ToolMode.LPG, ToolMode.HYBRID},
        "use_manager": True,
        "description": "Testing specific hypothesis"
    },
]
```

### 2. Add CLI option in `src/cli/evaluate.py`

```python
parser.add_argument("--custom", action="store_true", help="Run custom experiments")

if args.custom:
    from src.evaluation.experiments.custom import MY_EXPERIMENTS
    for exp in MY_EXPERIMENTS:
        run_experiment(exp["modes"], exp["use_manager"])
```

---

## 🗄️ Configuration

All paths and constants are in `src/config/settings.py`:

```python
# Add new configuration
MY_NEW_PATH = os.getenv("MY_NEW_PATH", "/workspace/default")
```

Database schemas are in `src/config/schemas.py`:

```python
MY_SCHEMA = """
### My Database Schema
...
"""
```

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/

# Run specific test
pytest tests/test_retrieval.py -v
```

---

## 📝 Code Style

- Use type hints
- Add docstrings to public functions
- Wrap database calls with `@track()` for observability
- Use `@function_tool` decorator for agent tools
