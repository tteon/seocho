FROM python:3.11-slim

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY extraction/requirements.txt /tmp/extraction-requirements.txt
RUN uv pip install --system --no-cache -r /tmp/extraction-requirements.txt

# extraction/ is a real package (seocho-60u), so it must land under its own
# directory rather than being flattened into /app. Flattening is what let
# `import config` and `import extraction.config` both resolve, loading the
# module twice as two objects with two DatabaseRegistry singletons.
COPY extraction/ /app/extraction/
COPY runtime/ /app/runtime/
COPY src/seocho/ /app/seocho/

RUN chmod +x /app/extraction/entrypoint.sh

CMD ["/bin/bash", "./extraction/entrypoint.sh"]
