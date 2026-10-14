"""The Director conversation is kept per project, on this computer, beside project.json."""
import json

from fastapi.testclient import TestClient

from poc.server.app import CHAT_LIMIT, create_app

H = {"X-Editor-Token": "t"}


def _client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path / "d", token="t", allowed_hosts=("testserver",)))


def test_new_project_has_an_empty_conversation_and_it_round_trips(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/api/projects", json={"name": "p"}, headers=H).json()["project_id"]
        assert c.get(f"/api/projects/{pid}/chat", headers=H).json() == {"messages": []}
        messages = [{"id": "m1", "role": "user", "text": "add fades"},
                    {"id": "m2", "role": "director", "kind": "proposal", "lines": ["Fade between clips."],
                     "status": "applied"}]
        assert c.put(f"/api/projects/{pid}/chat", json={"messages": messages}, headers=H).json() == {"saved": 2}
        assert c.get(f"/api/projects/{pid}/chat", headers=H).json() == {"messages": messages}
        stored = json.loads((tmp_path / "d" / "projects" / pid / "chat.json").read_text(encoding="utf-8"))
        assert stored["messages"] == messages
        assert not list((tmp_path / "d" / "projects" / pid).glob("*.partial"))


def test_only_the_latest_messages_are_kept(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/api/projects", json={"name": "p"}, headers=H).json()["project_id"]
        many = [{"id": f"m{i}", "role": "user", "text": str(i)} for i in range(CHAT_LIMIT + 20)]
        assert c.put(f"/api/projects/{pid}/chat", json={"messages": many}, headers=H).json() == {"saved": CHAT_LIMIT}
        kept = c.get(f"/api/projects/{pid}/chat", headers=H).json()["messages"]
        assert kept[0]["id"] == "m20" and kept[-1]["id"] == f"m{CHAT_LIMIT + 19}"


def test_bad_input_and_unknown_projects_are_refused(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/api/projects", json={"name": "p"}, headers=H).json()["project_id"]
        assert c.put(f"/api/projects/{pid}/chat", json={"messages": "hi"}, headers=H).status_code == 422
        assert c.put(f"/api/projects/{pid}/chat", json={"messages": [1, 2]}, headers=H).status_code == 422
        big = [{"id": "x", "text": "a" * 1_000_000}] * 3
        assert c.put(f"/api/projects/{pid}/chat", json={"messages": big}, headers=H).status_code == 413
        assert c.get("/api/projects/p_00000000/chat", headers=H).status_code == 404
        assert c.get(f"/api/projects/{pid}/chat").status_code in (401, 403)


def test_a_damaged_chat_file_reads_as_empty(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/api/projects", json={"name": "p"}, headers=H).json()["project_id"]
        (tmp_path / "d" / "projects" / pid / "chat.json").write_text("{not json", encoding="utf-8")
        assert c.get(f"/api/projects/{pid}/chat", headers=H).json() == {"messages": []}
