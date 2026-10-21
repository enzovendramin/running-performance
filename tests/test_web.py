"""The dashboard pages, end to end."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coach import matching, plan_import
from coach.db import get_connection, utcnow
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


def _log_sync(hours_ago: float) -> None:
    ran = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn = get_connection()
    conn.execute("INSERT INTO sync_log (ran_at, status, source) VALUES (?, 'ok', 'garmin_sync')",
                 (ran,))
    conn.commit()
    conn.close()


def test_stale_data_banner(client: TestClient) -> None:
    assert "Ainda sem dados do Garmin" in client.get("/").text   # never synced
    _log_sync(hours_ago=24 * 38)
    assert "Dados desatualizados" in client.get("/").text        # last good sync is old
    _log_sync(hours_ago=1)
    html = client.get("/").text
    assert "Dados desatualizados" not in html and "Ainda sem dados" not in html


def test_alert_is_marked_on_the_chart_listed_and_can_be_read(client: TestClient) -> None:
    conn = get_connection()
    for i in range(10):
        d = (date.today() - timedelta(days=i)).isoformat()
        conn.execute("INSERT INTO daily_load VALUES (?, 0, 10, 10, 0, 'x')", (d,))
    conn.execute(
        "INSERT INTO inbox_message (type, title, body, status, created_at)"
        " VALUES ('risk_alert', 'Fadiga acumulada', '<img src=x onerror=alert(1)>', 'unread', ?)",
        (utcnow(),))
    conn.commit()
    conn.close()
    page = client.get("/carga").text
    assert 'class="ev-mk' in page                     # marker on the load chart
    assert "Histórico de alertas" in page and "novo" in page
    assert "<img src=x" not in page                   # alert text is escaped everywhere
    r = client.post("/alertas/lido?id=todos", follow_redirects=False)
    assert r.headers["location"] == "/carga#alertas"
    assert "Marcar como lido" not in client.get("/carga").text


def test_planned_vs_actual_counts_completed_runs(client: TestClient) -> None:
    conn = get_connection()
    monday = date.today() - timedelta(days=date.today().weekday() + 14)  # two weeks ago
    days = [(monday + timedelta(days=k)).isoformat() for k in (1, 3)]
    pid = plan_import.import_plan(conn, (
        f'{{"week_start": "{monday.isoformat()}", "workouts": ['
        f'{{"date": "{days[0]}", "type": "easy", "km": 5}},'
        f'{{"date": "{days[1]}", "type": "easy", "km": 5}}]}}'))["plan_id"]
    plan_import.approve_plan(conn, pid)
    now = utcnow()
    conn.execute("INSERT INTO activity (garmin_activity_id, start_time_utc, start_time_local,"
                 " type, distance_m, synced_at, created_at) VALUES (1, ?, ?, 'running', 5000, ?, ?)",
                 (now, f"{days[0]} 08:00:00", now, now))
    conn.commit()
    matching.apply_matching(conn)
    conn.close()
    page = client.get("/treinos").text
    assert "Planejado vs. realizado" in page
    assert "1 de 2 corridas planejadas cumpridas (50%)" in page


def test_approving_a_pending_plan_from_the_reports_page(client: TestClient) -> None:
    conn = get_connection()
    pid = plan_import.import_plan(conn, '{"week_start": "2099-01-05", "workouts": [{"date":'
                                        ' "2099-01-06", "type": "easy", "km": 4}]}')["plan_id"]
    conn.close()
    assert "Planos aguardando aprovação" in client.get("/relatorios").text
    r = client.post(f"/importar/aprovar?id={pid}", follow_redirects=False)
    assert r.headers["location"] == "/relatorios?aprovado=1"
    assert "Planos aguardando aprovação" not in client.get("/relatorios").text
