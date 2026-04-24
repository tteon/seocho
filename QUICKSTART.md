# SEOCHO Quickstart

Fastest GitHub-first path to one successful SEOCHO run.

For the full runtime onboarding guide, see [docs/QUICKSTART.md](docs/QUICKSTART.md).
For the SDK-focused guide, see [docs/PYTHON_INTERFACE_QUICKSTART.md](docs/PYTHON_INTERFACE_QUICKSTART.md).
For a runnable notebook, see [examples/quickstart.ipynb](examples/quickstart.ipynb).

## Option A: Local SDK

```bash
uv pip install "seocho[local]"
```

```python
from seocho import Seocho, Ontology, NodeDef, RelDef, Property

ontology = Ontology(
    name="demo",
    nodes={
        "Person": NodeDef(properties={"name": Property(str, unique=True)}),
        "Company": NodeDef(properties={"name": Property(str, unique=True)}),
    },
    relationships={
        "WORKS_AT": RelDef(source="Person", target="Company"),
    },
)

client = Seocho.local(ontology)
client.add("Marie Curie worked at the University of Paris.")
print(client.ask("Where did Marie Curie work?"))
```

Use this path when you want the shortest ontology-first hello world with the
embedded LadybugDB default.

## Option B: Local Runtime

```bash
git clone https://github.com/tteon/seocho.git
cd seocho
make setup-env
make up
```

Then open:

- UI: `http://localhost:8501`
- API docs: `http://localhost:8001/docs`
- DozerDB browser: `http://localhost:7474`

## Option C: Remote Client

```python
from seocho import Seocho

client = Seocho.remote("http://localhost:8001")
print(client.ask("What do we know about ACME?"))
```

## Next

- [docs/QUICKSTART.md](docs/QUICKSTART.md): full runtime first-run flow
- [docs/PYTHON_INTERFACE_QUICKSTART.md](docs/PYTHON_INTERFACE_QUICKSTART.md): SDK path and parameters
- [docs/APPLY_YOUR_DATA.md](docs/APPLY_YOUR_DATA.md): bring your own ontology and records
- [docs/FILES_AND_ARTIFACTS.md](docs/FILES_AND_ARTIFACTS.md): where ontology files, artifacts, and traces go
