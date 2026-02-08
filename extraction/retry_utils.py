"""
Tenacity-based retry decorators for transient failures.

Provides pre-configured retry policies for OpenAI and Neo4j operations.
"""

import logging

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from exceptions import OpenAIAPIError, Neo4jConnectionError

logger = logging.getLogger(__name__)

openai_retry = retry(
    retry=retry_if_exception_type(OpenAIAPIError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)

neo4j_retry = retry(
    retry=retry_if_exception_type(Neo4jConnectionError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
