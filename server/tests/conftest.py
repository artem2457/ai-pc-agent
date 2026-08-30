import os

import pytest
from fastapi.testclient import TestClient

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "ci-test-secret"

from app.db import init_db
from app.main import app


@pytest.fixture
def client():
    init_db()
    with TestClient(app) as test_client:
        yield test_client
