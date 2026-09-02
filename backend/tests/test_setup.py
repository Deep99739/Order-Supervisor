from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_is_independent_and_readiness_is_honest():
    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1:1/test",
        temporal_address="127.0.0.1:1",
        agent_mode="live",
        model_provider="",
        model_name="",
        model_api_key="",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 200
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
        assert response.json()["temporal"] == "unavailable"
        assert response.json()["worker"] == "not_checked"
        assert response.json()["model"] == "missing_configuration"
        assert "test:test" not in response.text
        schema = client.get("/openapi.json").json()
        assert set(schema["paths"]) == {"/healthz", "/readyz"}
        cors = client.get("/healthz", headers={"Origin": settings.allowed_ui_origin})
        assert cors.headers["access-control-allow-origin"] == settings.allowed_ui_origin
        denied = client.get("/healthz", headers={"Origin": "https://unrelated.example"})
        assert "access-control-allow-origin" not in denied.headers
