"""
Custom exception hierarchy for SEOCHO.

Provides typed exceptions for structured error handling across the
extraction pipeline, agent server, and infrastructure layers.

Hierarchy:
    SeochoError (base)
    ├── InfrastructureError (transient — retry-eligible)
    │   ├── OpenAIAPIError
    │   └── Neo4jConnectionError
    ├── ConfigurationError (permanent — startup / misconfiguration)
    │   ├── MissingAPIKeyError
    │   └── InvalidDatabaseNameError
    ├── DataValidationError (permanent — bad input data)
    │   ├── InvalidLabelError
    │   └── OntologyError
    └── PipelineError (processing failures)
        ├── ExtractionError
        ├── LinkingError
        ├── DeduplicationError
        └── LoadError
"""


class SeochoError(Exception):
    """Base exception for all SEOCHO errors."""


# ---------------------------------------------------------------------------
# Infrastructure (transient)
# ---------------------------------------------------------------------------

class InfrastructureError(SeochoError):
    """Transient infrastructure failure (network, service unavailable)."""


class OpenAIAPIError(InfrastructureError):
    """OpenAI API call failed (rate-limit, timeout, server error)."""


class Neo4jConnectionError(InfrastructureError):
    """Neo4j driver/session failure (connectivity, session expired)."""


# ---------------------------------------------------------------------------
# Configuration (permanent)
# ---------------------------------------------------------------------------

class ConfigurationError(SeochoError):
    """Permanent configuration or environment error."""


class MissingAPIKeyError(ConfigurationError):
    """Required API key is missing or empty."""


class InvalidDatabaseNameError(ConfigurationError):
    """Database name does not match validation regex."""


# ---------------------------------------------------------------------------
# Data validation (permanent)
# ---------------------------------------------------------------------------

class DataValidationError(SeochoError):
    """Input data failed validation."""


class InvalidLabelError(DataValidationError):
    """Neo4j label or relationship type failed regex validation."""


class OntologyError(DataValidationError):
    """Ontology YAML is malformed or missing required fields."""


# ---------------------------------------------------------------------------
# Pipeline (processing)
# ---------------------------------------------------------------------------

class PipelineError(SeochoError):
    """Error during pipeline processing of a data item."""


class ExtractionError(PipelineError):
    """Entity extraction failed."""


class LinkingError(PipelineError):
    """Entity linking failed."""


class DeduplicationError(PipelineError):
    """Deduplication step failed."""


class LoadError(PipelineError):
    """Graph loading into Neo4j failed."""
