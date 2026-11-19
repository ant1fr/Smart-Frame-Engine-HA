"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load ``custom_components/`` in every test."""


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Stop config-flow tests from actually setting the entry up."""
    with patch("custom_components.smart_frame_engine.async_setup_entry", return_value=True) as mock:
        yield mock
