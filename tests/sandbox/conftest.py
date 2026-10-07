from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sandbox.quickbite.app import create_app
from sandbox.quickbite.db import Database
from sandbox.quickbite.seed import OPERATOR_PASSWORD, OPERATOR_USERNAME

CONTROL = {"X-Control-Key": "test-key"}


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(tmp_path / "quickbite.db", control_key="test-key")


@pytest.fixture
def db(app: FastAPI) -> Database:
    database: Database = app.state.sandbox.db
    return database


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def login(client: TestClient) -> Callable[..., TestClient]:
    def _login(*systems: str) -> TestClient:
        for system in systems or ("support", "ops", "payments"):
            response = client.post(
                f"/{system}/login", data={"username": OPERATOR_USERNAME, "password": OPERATOR_PASSWORD}
            )
            assert response.status_code == 200, response.text
        return client

    return _login
