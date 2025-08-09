FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install required packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY ingest_data.py .
COPY sample_supply_chain_data.json /sharepoint/

# Make the script executable
RUN chmod +x ingest_data.py

# Set environment variables
ENV PYTHONPATH=/app

# Default command
CMD ["python", "ingest_data.py"]