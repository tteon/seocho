# Integration Tests

This directory contains integration tests for the core workflows:

## Test Coverage

### `test_indexing.py`
- GraphAgent Indexing (Neo4j LPG and RDF)
- HybridAgent Indexing (LanceDB)
- Database connectivity and data verification

### `test_extraction.py`
- Pipeline extraction (RDF + LPG)
- Grounding verification
- Output format validation

### `test_agent_evaluation.py`
- Agent setup and configuration
- Tool routing and execution
- Evaluation metrics
- E2E agent execution

## Running Tests

### Inside Docker Container
```bash
docker exec -it agent-jupyter-container bash
cd /workspace
pytest tests/integration/ -v
```

### Run Specific Test Suite
```bash
pytest tests/integration/test_indexing.py -v
pytest tests/integration/test_extraction.py -v
pytest tests/integration/test_agent_evaluation.py -v
```

### Run with Coverage
```bash
pytest tests/integration/ --cov=src --cov-report=html
```

## Requirements

Tests require:
- Neo4j running (`graphrag-neo4j` container)
- LanceDB indexed data
- Opik instance (for evaluation tests)
- OpenAI API key (for agent tests)

## Skipping Tests

Tests that require external services are marked with `@pytest.mark.skipif` and will be skipped if the service is not available.
