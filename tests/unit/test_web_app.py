# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from pathlib import Path

from fastapi.testclient import TestClient

from windcode.sessions import SessionStore
from windcode.web.app import create_web_app


def test_web_rejects_non_loopback_host_and_origin(tmp_path: Path) -> None:
    app = create_web_app(web_root=tmp_path / "web")
    with TestClient(app) as client:
        assert client.get("/api/v1/workspaces", headers={"host": "example.com"}).status_code == 403
        assert (
            client.get(
                "/api/v1/workspaces",
                headers={"origin": "http://localhost.example.com"},
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/v1/workspaces",
                headers={"origin": "http://localhost:8765"},
            ).status_code
            == 200
        )
        assert client.get("/api/v1/unknown").status_code == 404


def test_web_workspace_crud_does_not_require_model_configuration(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    app = create_web_app(web_root=tmp_path / "web")

    with TestClient(app) as client:
        created = client.post("/api/v1/workspaces", json={"path": str(workspace)})
        assert created.status_code == 200
        workspace_id = created.json()["id"]
        listed = client.get("/api/v1/workspaces").json()
        assert listed["selected"] == workspace_id
        assert listed["items"][0]["path"] == str(workspace)

        removed = client.delete(f"/api/v1/workspaces/{workspace_id}")
        assert removed.status_code == 200
        assert client.get("/api/v1/workspaces").json()["items"] == []


def test_web_directory_browser_returns_directories_only(tmp_path: Path) -> None:
    root = tmp_path / "root"
    project = root / "project"
    project.mkdir(parents=True)
    (root / "note.txt").write_text("not a directory", encoding="utf-8")
    app = create_web_app(web_root=tmp_path / "web")

    with TestClient(app) as client:
        response = client.get("/api/v1/directories", params={"path": str(root)})

    assert response.status_code == 200
    assert response.json() == {
        "path": str(root),
        "parent": str(tmp_path),
        "items": [{"name": "project", "path": str(project)}],
    }


def test_websocket_event_endpoint_accepts_loopback_client(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    app = create_web_app(
        initial_workspace=workspace,
        web_root=tmp_path / "web",
        state_root=tmp_path / "state",
    )

    with TestClient(app) as client:
        workspace_id = client.get("/api/v1/workspaces").json()["selected"]
        with client.websocket_connect(
            f"/api/v1/events?workspace_id={workspace_id}&after=0",
            headers={"origin": "http://localhost:8765"},
        ) as websocket:
            websocket.close()


def test_web_exposes_the_shared_tui_command_catalog(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    app = create_web_app(initial_workspace=workspace, web_root=tmp_path / "web")

    with TestClient(app) as client:
        workspace_id = client.get("/api/v1/workspaces").json()["selected"]
        response = client.get(f"/api/v1/workspaces/{workspace_id}/commands")

    assert response.status_code == 200
    assert {item["name"] for item in response.json()["items"]} >= {"new", "help", "status"}


def test_web_can_clear_workspace_session_history(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    app = create_web_app(
        initial_workspace=workspace,
        web_root=tmp_path / "web",
        state_root=tmp_path / "state",
    )

    with TestClient(app) as client:
        workspace_id = client.get("/api/v1/workspaces").json()["selected"]
        client.get(f"/api/v1/workspaces/{workspace_id}/sessions")
        sessions_root = tmp_path / "state" / "sessions"
        SessionStore.create(sessions_root, "one")
        SessionStore.create(sessions_root, "two")

        response = client.delete(f"/api/v1/workspaces/{workspace_id}/sessions")

        assert response.status_code == 200
        assert response.json() == {"deleted": 2}
        assert client.get(f"/api/v1/workspaces/{workspace_id}/sessions").json() == []
        archived = list((sessions_root / ".archive").glob("*/*"))
        assert {path.name for path in archived} == {"one", "two"}
