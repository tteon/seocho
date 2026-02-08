"""Tests for config validation."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch

from exceptions import MissingAPIKeyError


class TestValidateConfig:
    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            from config import validate_config
            with pytest.raises(MissingAPIKeyError, match="OPENAI_API_KEY"):
                validate_config()

    def test_valid_config_passes(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}, clear=False):
            from config import validate_config
            # Should not raise
            validate_config()

    def test_absent_api_key_raises(self):
        env = os.environ.copy()
        env.pop("OPENAI_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            from config import validate_config
            with pytest.raises(MissingAPIKeyError):
                validate_config()
