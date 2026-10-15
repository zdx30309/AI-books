import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import tasks as router
from test_similarity_gate_api import fixture


def test_excel_download_is_header_auth_only():
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        response = client.get('/api/v1/tasks/00000000-0000-0000-0000-000000000001/corrections.xlsx?token=not-a-header')
    assert response.status_code == 401
    route = app.openapi()["paths"]["/api/v1/tasks/{task_id}/corrections.xlsx"]["get"]
    assert not any(parameter["name"] == "token" for parameter in route.get("parameters", []))


def test_excel_owner_download_and_path_escape_protection(fixture):
    current = fixture
    directory = current.directory / "editor_v05/ai_or_finalizer"
    directory.mkdir(parents=True)
    (directory / "correction.xlsx").write_bytes(b"excel-test-bytes")
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": {"xlsx": "correction.xlsx"}}))
    url = f"/api/v1/tasks/{current.task.id}/corrections.xlsx"
    response = current.client.get(url)
    assert response.status_code == 200 and response.content == b"excel-test-bytes"
    assert response.headers["cache-control"] == "no-store"
    manifest.write_text(json.dumps({"artifacts": {"xlsx": "../correction.xlsx"}}))
    assert current.client.get(url).status_code == 404
    manifest.write_text(json.dumps({"artifacts": {"xlsx": "correction.xlsx"}}))
    from uuid import uuid4
    current.task.owner_id = uuid4()
    current.session.commit()
    assert current.client.get(url).status_code == 404
