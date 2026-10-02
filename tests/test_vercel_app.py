"""Smoke tests for the Vercel-compatible Flask UI (api/index.py)."""
from pathlib import Path

import pytest


@pytest.fixture
def flask_client(tmp_path, monkeypatch):
    import api.index as mod
    from config import Settings

    monkeypatch.setattr(
        mod,
        "settings",
        Settings(
            ai_provider="mock",
            database_url=f"sqlite:///{tmp_path / 'vercel.db'}",
            app_env="test",
        ),
    )
    monkeypatch.setattr(mod, "_service", None)
    app = mod.app
    app.config.update(TESTING=True)
    return app.test_client(), mod


def test_flask_app_renders_all_pages(flask_client):
    client, _ = flask_client
    for path in ["/", "/dashboard", "/leads", "/import", "/new", "/settings", "/health"]:
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)


def test_flask_seeds_sample_data_and_lead_detail(flask_client):
    client, _ = flask_client
    response = client.get("/leads")
    assert b"L-1001" in response.data
    detail = client.get("/lead/L-1001")
    assert detail.status_code == 200
    assert b"AI analysis" in detail.data


def test_flask_analyze_and_record_outcome(flask_client):
    client, mod = flask_client
    # triggers seeding on first request
    client.get("/")
    response = client.post("/lead/L-1001/analyze", follow_redirects=True)
    assert response.status_code == 200

    outcome = client.post(
        "/lead/L-1001/outcome",
        data={"activity": "Call", "outcome": "No response", "notes": "vm", "stage": "(unchanged)", "status": "(unchanged)"},
        follow_redirects=True,
    )
    assert outcome.status_code == 200
    assert b"Next follow-up recalculated" in outcome.data


def test_flask_new_lead_and_settings(flask_client):
    client, _ = flask_client
    created = client.post(
        "/new",
        data={"name": "Pat Test", "inquiry": "We need help with leads.", "stage": "New", "status": "Not Contacted"},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"Pat Test" in created.data

    saved = client.post(
        "/settings",
        data={
            "business_name": "Acme",
            "description": "We automate follow-up.",
            "services": "Assessment",
            "tone": "Friendly",
            "allowed_claims": "We offer assessments.",
            "restricted_topics": "No prices.",
            "follow_up_rules": "2 days",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert b"Business profile saved" in saved.data


def test_flask_import_page_available(flask_client):
    client, _ = flask_client
    response = client.get("/import")
    assert b"Import leads" in response.data
