"""Tests for the custom exception hierarchy."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from exceptions import (
    SeochoError,
    InfrastructureError,
    OpenAIAPIError,
    Neo4jConnectionError,
    ConfigurationError,
    MissingAPIKeyError,
    InvalidDatabaseNameError,
    DataValidationError,
    InvalidLabelError,
    OntologyError,
    PipelineError,
    ExtractionError,
    LinkingError,
    DeduplicationError,
    LoadError,
)


class TestExceptionHierarchy:
    def test_base_exception(self):
        exc = SeochoError("base error")
        assert str(exc) == "base error"
        assert isinstance(exc, Exception)

    def test_infrastructure_is_seocho(self):
        assert issubclass(InfrastructureError, SeochoError)

    def test_openai_api_error_hierarchy(self):
        exc = OpenAIAPIError("rate limited")
        assert isinstance(exc, InfrastructureError)
        assert isinstance(exc, SeochoError)
        assert str(exc) == "rate limited"

    def test_neo4j_connection_error_hierarchy(self):
        exc = Neo4jConnectionError("connection refused")
        assert isinstance(exc, InfrastructureError)
        assert isinstance(exc, SeochoError)

    def test_configuration_error_hierarchy(self):
        assert issubclass(MissingAPIKeyError, ConfigurationError)
        assert issubclass(InvalidDatabaseNameError, ConfigurationError)
        assert issubclass(ConfigurationError, SeochoError)

    def test_data_validation_hierarchy(self):
        assert issubclass(InvalidLabelError, DataValidationError)
        assert issubclass(OntologyError, DataValidationError)
        assert issubclass(DataValidationError, SeochoError)

    def test_pipeline_error_hierarchy(self):
        assert issubclass(ExtractionError, PipelineError)
        assert issubclass(LinkingError, PipelineError)
        assert issubclass(DeduplicationError, PipelineError)
        assert issubclass(LoadError, PipelineError)
        assert issubclass(PipelineError, SeochoError)

    def test_exception_messages_preserved(self):
        exc = MissingAPIKeyError("OPENAI_API_KEY not set")
        assert "OPENAI_API_KEY" in str(exc)

    def test_exceptions_are_catchable_by_base(self):
        """All typed exceptions should be catchable via SeochoError."""
        exceptions = [
            OpenAIAPIError("test"),
            Neo4jConnectionError("test"),
            MissingAPIKeyError("test"),
            InvalidDatabaseNameError("test"),
            InvalidLabelError("test"),
            OntologyError("test"),
            ExtractionError("test"),
            LinkingError("test"),
            DeduplicationError("test"),
            LoadError("test"),
        ]
        for exc in exceptions:
            try:
                raise exc
            except SeochoError:
                pass  # expected
