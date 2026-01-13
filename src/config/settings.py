"""
Centralized configuration for the Graph RAG system.
All paths, credentials, and constants are defined here.
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ===========================================
# Paths
# ===========================================
LANCEDB_PATH = os.getenv("LANCEDB_PATH", "/workspace/data/lancedb")
LANCEDB_TABLE = "fibo_context"

# Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://graphrag-neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
LPG_DATABASE = "lpg"
RDF_DATABASE = "rdf"

# Opik
OPIK_URL = os.getenv("OPIK_URL_OVERRIDE", "http://localhost:5173/api")
OPIK_WORKSPACE = os.getenv("OPIK_WORKSPACE", "default")
OPIK_PROJECT = os.getenv("OPIK_PROJECT_NAME", "graph-agent")

# Output directories
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/workspace/output")
EXPORT_OPIK_DIR = os.getenv("EXPORT_OPIK_DIR", "/workspace/export_opik")
CACHE_DIR = os.getenv("OPENAI_CACHE_DIR", "/workspace/.openai_cache")
KGBUILD_TRACES_PATH = os.getenv("KGBUILD_TRACES_PATH", "/workspace/kgbuild-traces.json")

# ===========================================
# Models
# ===========================================
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
LLM_MODEL = "gpt-4o"

# ===========================================
# Indexing
# ===========================================
BATCH_SIZE = 100
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
