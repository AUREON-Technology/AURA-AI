"""The in-memory ``CloudSandboxStore`` (the Postgres one runs the same cases in ``tests/load``)."""

from __future__ import annotations

import pytest

from aura_core.adapters.memory_stores import InMemoryCloudSandboxStore
from aura_core.ports.coding import CloudSandboxStore

from tests.cloud_sandbox_store_cases import CloudSandboxStoreCases


class TestInMemoryCloudSandboxStore(CloudSandboxStoreCases):
    @pytest.fixture
    def store(self) -> InMemoryCloudSandboxStore:
        return InMemoryCloudSandboxStore()

    def test_it_is_the_port(self, store) -> None:
        assert isinstance(store, CloudSandboxStore)
