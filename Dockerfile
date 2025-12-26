FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Jupyter and Python libraries
RUN pip install --no-cache-dir \
    jupyterlab \
    openai \
    datasets \
    pandas \
    numpy \
    opik \
    ipywidgets \
    python-dotenv

# Set working directory
WORKDIR /workspace

# Expose Jupyter port
EXPOSE 8888

# Start Jupyter Lab
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", "--NotebookApp.token=''", "--NotebookApp.password=''"]
