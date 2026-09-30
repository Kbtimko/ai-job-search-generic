import json
from pathlib import Path

import pytest

from scripts.tracker import create_app
import scripts.store as store


def _app(tmp_path):
    apps = tmp_path / "applications.json"
    jobs = tmp_path / "jobs.json"
    apps.write_text(json.dumps([{"company": "Etsy", "title": "Director", "stage": "draft",
        "applied_date": None,
        "stage_history": [{"stage": "draft", "date": "2026-07-01", "source": "tailor"}]}]))
    jobs.write_text("[]")
    app = create_app(applications_path=apps, jobs_path=jobs)
    app.config.update(TESTING=True)
    return app


def _auth_app(tmp_path, monkeypatch, allowed="me@gmail.com"):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("ALLOWED_EMAILS", allowed)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    app = _app(tmp_path)
    return app


def test_unauthenticated_redirected_to_login(tmp_path, monkeypatch):
    app = _auth_app(tmp_path, monkeypatch)
    resp = app.test_client().get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_allowlisted_session_can_view(tmp_path, monkeypatch):
    app = _auth_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "me@gmail.com"
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Etsy" in resp.get_data(as_text=True)


def test_non_allowlisted_session_refused(tmp_path, monkeypatch):
    app = _auth_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_email"] = "stranger@evil.com"
    resp = client.get("/")
    assert resp.status_code == 403


def test_auth_disabled_leaves_app_open(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    app = _app(tmp_path)
    assert app.test_client().get("/").status_code == 200


def test_store_conflict_flashes_and_redirects(tmp_path, monkeypatch):
    app = _app(tmp_path)
    def _boom(path, items):
        raise store.StoreConflict("data/applications.json")
    monkeypatch.setattr("scripts.tracker.store.save_store", _boom)
    client = app.test_client()
    resp = client.post("/advance", data={"key": "etsy::director", "stage": "applied"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert "changed" in resp.get_data(as_text=True).lower()
