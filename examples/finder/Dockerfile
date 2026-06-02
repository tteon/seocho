# Image for running the FinDER + FIBO + RDF/LPG tutorial notebooks.
#
# Provides JupyterLab with:
#   - the seocho package and ci extras (lancedb, faiss-cpu, neo4j, openai)
#   - tutorial-only deps (jupyterlab, networkx, matplotlib, pandas)
#
# Build context must be the repo root so src/seocho, pyproject.toml, and
# examples/finder/requirements.txt are all reachable.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# uv for fast resolves (matches the pattern used by extraction/Dockerfile)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Stage 1 — bake an installation of the seocho package with the ci extra
# (lancedb, faiss-cpu, neo4j driver, openai, etc.).  At runtime the source
# tree is bind-mounted over /workspace, and PYTHONPATH=/workspace/src makes
# the mounted src/seocho package shadow this baked copy.
WORKDIR /tmp/build
COPY pyproject.toml /tmp/build/pyproject.toml
COPY README.md /tmp/build/README.md
COPY src /tmp/build/src
RUN uv pip install --system --no-cache ".[ci,local]"

# Stage 2 — tutorial-only Python deps (jupyterlab, networkx, matplotlib, …)
COPY examples/finder/requirements.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache -r /tmp/requirements.txt

WORKDIR /workspace
ENV PYTHONPATH=/workspace/src:/workspace

EXPOSE 8889

CMD ["jupyter", "lab", \
     "--ip=0.0.0.0", \
     "--port=8889", \
     "--no-browser", \
     "--allow-root", \
     "--ServerApp.token=", \
     "--ServerApp.password=", \
     "--ServerApp.root_dir=/workspace", \
     "--ServerApp.default_url=/lab/tree/examples/finder"]
