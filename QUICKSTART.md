# SEOCHO Quickstart

This is the shortest path to a working ontology-aligned graph memory.

You will:

1. create a tiny runnable SEOCHO project
2. run offline preflight
3. index documents, ask questions, and open the report

## 1. Install

```bash
uv pip install "seocho[local]"
```

`seocho[local]` includes the local SDK engine, agent dependencies, and the
embedded LadybugDB graph path. You do not need to run a server for this first
example.

Set your provider key. SEOCHO recommends MARA:

```bash
export MARA_API_KEY=...
```

Prefer OpenAI/DeepSeek/Kimi? Export that provider's key and swap the `llm=`
string below (`"openai/gpt-4o"`, `"deepseek/deepseek-chat"`, `"kimi/kimi-k2.5"`).

## 2. Create And Run A Project

```bash
seocho new hello-seocho
cd hello-seocho
seocho run --dry-run
seocho run
```

From a cloned repository, prefix CLI commands with `uv run`:

```bash
uv run seocho new hello-seocho
cd hello-seocho
uv run seocho run --dry-run
uv run seocho run
```

What happened:

- `schema.yaml` declared the allowed graph shape
- `docs/` provided source notes to index
- `seocho.run.yaml` declared the questions
- `report.md` and `report.json` captured answers, support status, missing
  slots, and selected graph evidence

## 3. The Smallest SDK Example

```python
from seocho import Seocho, Ontology, NodeDef, RelDef, Property

ontology = Ontology(
    name="work",
    nodes={
        "Person": NodeDef(properties={"name": Property(str, unique=True)}),
        "Company": NodeDef(properties={"name": Property(str, unique=True)}),
    },
    relationships={
        "WORKS_AT": RelDef(source="Person", target="Company"),
    },
)

client = Seocho.local(ontology, llm="mara/MiniMax-M2.5")
client.add("Marie Curie worked at the University of Paris.")

print(client.ask("Where did Marie Curie work?"))
```

What happened:

- the ontology declared the allowed graph shape
- `add()` extracted graph facts that fit that shape
- `ask()` queried the graph memory and produced an ontology-grounded answer

## 4. Run A Domain Example

The finance-compliance example is the fastest complete project-shaped path:

```bash
export MARA_API_KEY=...
uv run python examples/finance-compliance/quickstart.py
```

It ships:

- `examples/finance-compliance/ontology.py`
- six short mock compliance documents
- a script that ingests them and asks cross-document questions

## 5. Connect To A Runtime

If a SEOCHO runtime is already running:

```python
from seocho import Seocho

client = Seocho.remote("http://localhost:8001")
print(client.ask("What do we know about ACME?"))
```

To start the local UI/API/DozerDB stack from a cloned repository:

```bash
make setup-env
make up
```

Then open:

- UI: `http://localhost:8501`
- API docs: `http://localhost:8001/docs`
- DozerDB browser: `http://localhost:7474`

## Next

| Need | Start here |
|---|---|
| Understand the project | [README.md](README.md) |
| Use your own ontology and files | [docs/APPLY_YOUR_DATA.md](docs/APPLY_YOUR_DATA.md) |
| Connect Notion, Slack, DataHub, Postgres, Neo4j/DozerDB, LangChain, or LlamaIndex | [docs/CONNECTORS.md](https://github.com/tteon/seocho/blob/main/docs/CONNECTORS.md) |
| Learn the Python SDK | [docs/PYTHON_INTERFACE_QUICKSTART.md](docs/PYTHON_INTERFACE_QUICKSTART.md) |
| Run the full platform | [docs/RUNTIME_DEPLOYMENT.md](docs/RUNTIME_DEPLOYMENT.md) |
| See generated files and traces | [docs/FILES_AND_ARTIFACTS.md](docs/FILES_AND_ARTIFACTS.md) |
