"""Built-in connector materializers.

The connector layer is intentionally small: it normalizes external ecosystem
data into SEOCHO-compatible records and JSONL files. Indexing, ontology
alignment, graph writes, and answering still flow through the existing SDK and
``seocho run`` paths.
"""

from .records import (
    ConnectorRecord,
    read_records_jsonl,
    records_from_langchain_documents,
    records_from_llamaindex_documents,
    summarize_records,
    write_records_jsonl,
)
from .config import (
    ConnectorRunPlan,
    ConnectorRunResult,
    ConnectorSourceSpec,
    load_connector_config,
    run_connector_plan,
    write_sample_config,
)

__all__ = [
    "ConnectorRecord",
    "ConnectorRunPlan",
    "ConnectorRunResult",
    "ConnectorSourceSpec",
    "load_connector_config",
    "read_records_jsonl",
    "records_from_langchain_documents",
    "records_from_llamaindex_documents",
    "run_connector_plan",
    "summarize_records",
    "write_sample_config",
    "write_records_jsonl",
]
