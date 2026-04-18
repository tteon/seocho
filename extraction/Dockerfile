FROM python:3.11-slim

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY extraction/requirements.txt /tmp/extraction-requirements.txt
RUN uv pip install --system --no-cache -r /tmp/extraction-requirements.txt

COPY extraction/ /app/
COPY runtime/ /app/runtime/
COPY seocho/ /app/seocho/
COPY notebooks/ /app/notebooks/
COPY demos/ /app/demos/

RUN chmod +x /app/entrypoint.sh

CMD ["/bin/bash", "./entrypoint.sh"]
