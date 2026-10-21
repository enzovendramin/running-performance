"""The dashboard pages, end to end."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coach import plan_import
from coach.db import get_connection
from coach.web.app import app

PAGES = ["/", "/carga", "/treinos", "/treinos?m=2026-08&w=35", "/relatorios",
         "/sincronizacao", "/importar", "/exportar"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app on an empty database file — like a fresh clone before the first sync."""
    monkeypatch.setenv("COACH_DB_PATH", str(tmp_path / "coach.db"))
    monkeypatch.setenv("COACH_EXPORT_DIR", str(tmp_path / "exports"))
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_every_page_opens_on_an_empty_database(client: TestClient, path: str) -> None:
    # regression: the overview and load pages crashed (500) without any data
    assert client.get(path).status_code == 200


def test_a_nonexistent_week_in_the_url_is_ignored(client: TestClient) -> None:
    assert client.get("/treinos?w=60").status_code == 200


def test_text_from_the_chat_is_shown_not_executed(client: TestClient) -> None:
    conn = get_connection()
    plan_import.import_plan(conn, '{"week_start": "2099-01-05", "workouts": [{"date":'
                                  ' "2099-01-06", "type": "easy", "km": 4,'
                                  ' "note": "<script>alert(1)</script>"}]}')
    conn.close()
    html = client.get("/").text
    assert "&lt;script&gt;alert(1)" in html and "<script>alert(1)" not in html


def test_approving_a_pending_plan_from_the_reports_page(client: TestClient) -> None:
    conn = get_connection()
    pid = plan_import.import_plan(conn, '{"week_start": "2099-01-05", "workouts": [{"date":'
                                        ' "2099-01-06", "type": "easy", "km": 4}]}')["plan_id"]
    conn.close()
    assert "Planos aguardando aprovação" in client.get("/relatorios").text
    r = client.post(f"/importar/aprovar?id={pid}", follow_redirects=False)
    assert r.headers["location"] == "/relatorios?aprovado=1"
    assert "Planos aguardando aprovação" not in client.get("/relatorios").text
