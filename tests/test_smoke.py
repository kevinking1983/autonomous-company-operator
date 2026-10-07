from fastapi.testclient import TestClient

from company_operator import __version__
from company_operator.api.app import app
from company_operator.config import Settings


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "company_pack": "quickbite"}


def test_company_pack_dir_exists() -> None:
    assert Settings().company_pack_dir.is_dir()
