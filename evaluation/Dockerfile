FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root, same reasoning as extraction/Dockerfile. This tier is the
# internet-facing one and is the most likely thing to be exploited.
RUN useradd --create-home --uid 10001 seocho \
    && chown -R seocho:seocho /app
USER seocho

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8501"]
