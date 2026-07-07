"""Local dashboard (deterministic): view and store Garmin-derived data and reports.

Five screens — Visão geral, Condição & carga, Treinos, Recuperação, Relatórios — served
over coach.db. Server-rendered HTML + inline SVG (charts.py) + one design system
(assets.py). It stores and shows; it makes no training decisions.

Run (offline):  python -m coach.web  ->  http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

from coach import alerts as alertmod
from coach.db import get_connection, run_migrations
from coach.web import charts
from coach.web.assets import CSS, JS

app = FastAPI(title="Coach")

# ---------------------------------------------------------------- helpers -----
PT_MONTH = {3: "Março", 4: "Abril", 5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto"}
PT_MONTH_SHORT = {3: "mar", 4: "abr", 5: "mai", 6: "jun", 7: "jul", 8: "ago"}
PT_WD = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "reports"


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _zone(hr: int | None) -> str:
    if hr is None:
        return "na"
    return "easy" if hr < 145 else ("mod" if hr <= 165 else "hard")


def _pace(spd: float | None) -> str | None:
    if not spd or spd <= 0:
        return None
    s = 1000 / spd
    return f"{int(s//60)}:{int(round(s%60)):02d}"


# ---------------------------------------------------------------- data --------
def load_data(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    prof = conn.execute("SELECT * FROM user_profile WHERE id=1").fetchone()
    acts = [dict(r) for r in conn.execute(
        "SELECT start_time_local, type, distance_m, duration_s, avg_hr, max_hr, avg_speed,"
        " aerobic_training_effect, anaerobic_training_effect FROM activity"
        " WHERE type='running' ORDER BY start_time_local")]
    for a in acts:
        a["d"] = date.fromisoformat(a["start_time_local"][:10])
        a["km"] = round((a["distance_m"] or 0) / 1000, 2)
        a["min"] = round((a["duration_s"] or 0) / 60, 1)
        a["pace"] = _pace(a["avg_speed"])
        a["zone"] = _zone(a["avg_hr"])

    load = [dict(r) for r in conn.execute(
        "SELECT date, daily_load, ctl, atl, tsb FROM daily_load ORDER BY date")]
    well = [dict(r) for r in conn.execute(
        "SELECT date, resting_hr, sleep_seconds, body_battery_high, body_battery_low,"
        " avg_stress FROM daily_wellness ORDER BY date")]

    # weekly (ISO) + monthly, each split by intensity zone
    def bucketize(keyfn, labelfn, shortfn):
        b = defaultdict(lambda: {"km": 0.0, "runs": 0, "easy": 0.0, "mod": 0.0, "hard": 0.0})
        for a in acts:
            k = keyfn(a["d"])
            b[k]["km"] += a["km"]; b[k]["runs"] += 1
            if a["zone"] in ("easy", "mod", "hard"):
                b[k][a["zone"]] += a["km"]
        return b

    wk = bucketize(lambda d: d.isocalendar()[1], None, None)
    weekly = []
    for n in range(11, 28):
        v = wk.get(n, {"km": 0.0, "runs": 0, "easy": 0.0, "mod": 0.0, "hard": 0.0})
        weekly.append({"label": f"Semana {n}", "short": f"S{n}", "n": n, **v})

    mo = bucketize(lambda d: d.month, None, None)
    monthly = []
    for m in sorted(mo):
        v = mo[m]
        monthly.append({"label": PT_MONTH[m], "short": PT_MONTH_SHORT[m], **v})

    rhr = [w for w in well if w["resting_hr"] is not None][-40:]
    bb = [w for w in well if w["body_battery_high"] is not None][-18:]

    # active plan + per-day done flag (matched by an activity on that date)
    plan = conn.execute("SELECT * FROM weekly_plan WHERE status IN ('active','proposed')"
                        " ORDER BY created_at DESC LIMIT 1").fetchone()
    pworkouts = []
    if plan:
        seen = set()
        for w in conn.execute("SELECT * FROM planned_workout WHERE weekly_plan_id=? ORDER BY date",
                              (plan["id"],)):
            if w["date"] in seen:  # de-dupe (older builds inserted twice)
                continue
            seen.add(w["date"])
            done = conn.execute("SELECT 1 FROM activity WHERE substr(start_time_local,1,10)=?"
                                " AND type='running' LIMIT 1", (w["date"],)).fetchone() is not None
            pworkouts.append({**dict(w), "done": done})

    fired = alertmod.evaluate(conn, date.today())
    recovery = alertmod.recovery_flags(conn)

    total_km = round(sum(a["km"] for a in acts), 1)
    n_weeks = 17
    per_week = round(len(acts) / n_weeks, 1)
    return {
        "profile": dict(prof) if prof else {},
        "runs": acts, "load": load, "weekly": weekly, "monthly": monthly,
        "rhr": rhr, "bb": bb, "plan": plan, "pworkouts": pworkouts,
        "alerts": fired, "recovery": recovery,
        "totals": {"km": total_km, "runs": len(acts), "per_week": per_week,
                   "ctl": load[-1]["ctl"] if load else 0, "atl": load[-1]["atl"] if load else 0,
                   "tsb": load[-1]["tsb"] if load else 0,
                   "ctl_peak": max((x["ctl"] for x in load), default=0),
                   "vo2max": (dict(prof).get("vo2max") if prof else None)},
    }


# ---------------------------------------------------------------- gauges ------
def build_gauges(t: dict[str, Any]) -> dict[str, str]:
    ctl, tsb, pw, vo2, peak = t["ctl"], t["tsb"], t["per_week"], t["vo2max"], t["ctl_peak"]
    ctl_tag = ("muito baixa", "warn") if ctl < 15 else (("moderada", "accent") if ctl < 30 else ("boa", "good"))
    tsb_tag = ("fatigado", "alert") if tsb < -10 else (("fresco", "accent") if tsb > 5 else ("equilibrada", "good"))
    pw_tag = ("abaixo da meta", "warn") if pw < 2.5 else (("no alvo", "good") if pw <= 4 else ("alto", "accent"))
    return {
        "ctl": charts.gauge(
            "Condição (fitness)", f"{ctl:.1f}", ctl_tag[0], ctl_tag[1], 0, 50, ctl,
            [(15, "var(--seq-1)"), (30, "var(--seq-2)"), (45, "var(--seq-3)"), (50, "var(--seq-4)")],
            ("destreinado", "em forma"), f"Carga que o corpo aguenta com folga · pico {peak:.1f}", peak),
        "tsb": charts.gauge(
            "Forma (frescor)", f"{tsb:+.1f}", tsb_tag[0], tsb_tag[1], -20, 20, tsb,
            [(-10, "var(--alert-weak)"), (5, "var(--surface-2)"), (20, "var(--good-weak)")],
            ("fatigado", "fresco"), "Fitness menos fadiga — perto de zero é neutro"),
        "week": charts.gauge(
            "Treinos por semana", f"{pw:.1f}", pw_tag[0], pw_tag[1], 0, 5, pw,
            [(2.5, "var(--warn-weak)"), (4, "var(--good-weak)"), (5, "var(--accent-weak)")],
            ("0", "5×"), "Meta para reconstruir a base: 3× por semana"),
        "vo2": charts.gauge(
            "VO₂max", f"{vo2:.0f}" if vo2 else "—", "elite", "good", 35, 65, vo2 or 35,
            [(42, "var(--seq-1)"), (50, "var(--seq-2)"), (58, "var(--seq-3)"), (65, "var(--seq-4)")],
            ("35", "elite"), "Seu motor — idade fitness 20"),
    }


PT_TYPE = {"easy": "fácil", "long": "longo", "rest": "descanso", "tempo": "tempo",
           "intervals": "tiros", "hard": "forte", "recovery": "regenerativo"}


def _plan_strip(t: dict[str, Any]) -> str:
    if not t["plan"] or not t["pworkouts"]:
        return '<p class="empty">Nenhum plano ativo. Monte um na conversa com o coach.</p>'
    cells = []
    for w in t["pworkouts"]:
        d = date.fromisoformat(w["date"])
        wd = PT_WD[d.weekday()]
        typ = PT_TYPE.get(w["type"], w["type"])
        km = f'{w["target_distance_m"]/1000:.0f} km' if w["target_distance_m"] else "&mdash;"
        cls = "rest" if w["type"] == "rest" else ("run done" if w["done"] else "run")
        cells.append(f'<div class="pday {cls}"><div class="pd">{wd} {d.day:02d}</div>'
                     f'<div class="pt">{typ}</div><div class="pk">{km}</div></div>')
    return f'<div class="plan">{"".join(cells)}</div>'


RECOVERY_PT = {
    "resting HR elevated": "FC de repouso elevada",
    "Body Battery low": "Body Battery baixa",
    "short sleep": "sono curto",
}


def _alerts_block(t: dict[str, Any]) -> str:
    rows = []
    for a in t["alerts"]:
        rows.append(f'<div class="alertrow"><span class="dot" style="background:var(--alert)"></span>'
                    f'<div><b>{_esc(a["title"])}</b><br>{_esc(a["message"])}</div></div>')
    if t["recovery"]:
        flags = ", ".join(RECOVERY_PT.get(f, f) for f in t["recovery"])
        rows.append(f'<div class="alertrow"><span class="dot" style="background:var(--warn)"></span>'
                    f'<div>Sinais de recuperação: {_esc(flags)}</div></div>')
    if not rows:
        return ('<div class="alertrow"><span class="dot" style="background:var(--good)"></span>'
                '<div>Tudo tranquilo — nenhum sinal de sobrecarga.</div></div>')
    return '<div class="rlist">' + "".join(rows) + "</div>"


INTENSITY_LEGEND = (
    '<div class="legend">'
    '<span><span class="key" style="background:var(--easy)"></span><b>fácil</b> &lt;145</span>'
    '<span><span class="key" style="background:var(--mod)"></span><b>moderado</b> 145–165</span>'
    '<span><span class="key" style="background:var(--hard)"></span><b>forte</b> &gt;165 bpm</span></div>')


# ---------------------------------------------------------------- pages -------
def page_overview(t: dict[str, Any]) -> str:
    g = build_gauges(t["totals"])
    return f"""
    <div class="pagehead"><p class="eyebrow">Painel</p><h1>Visão geral</h1>
      <p class="sub">Motor de elite, base a reconstruir. O essencial num relance — passe o mouse nos gráficos.</p></div>
    <div class="grid g4">
      <div class="card">{g['ctl']}</div><div class="card">{g['tsb']}</div>
      <div class="card">{g['week']}</div><div class="card">{g['vo2']}</div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card span2"><h2>Esta semana</h2><p class="hint">Plano ativo — verde = já feito.</p>
        {_plan_strip(t)}</div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h2>Alertas &amp; recuperação</h2>{_alerts_block(t)}</div>
      <div class="card"><h2>Condição &amp; fadiga</h2>
        <p class="hint">Fitness (roxo) vs fadiga (âmbar) — <a href="/carga" style="color:var(--accent)">ver detalhe</a>.</p>
        <div class="fig">{charts.load_lines(t['load'])}</div></div>
    </div>"""


def page_load(t: dict[str, Any]) -> str:
    return f"""
    <div class="pagehead"><p class="eyebrow">Carga de treino</p><h1>Condição &amp; carga</h1>
      <p class="sub">A fitness só subiu quando você emendou semanas — e evaporou no buraco de junho.</p></div>
    <div class="card"><h2>Condição × Fadiga · 4 meses</h2>
      <div class="legend"><span><span class="key" style="background:var(--accent)"></span><b>Condição</b> (fitness, 42d)</span>
        <span><span class="key" style="background:var(--mod)"></span><b>Fadiga</b> (7d)</span></div>
      <div class="fig">{charts.load_lines(t['load'])}</div>
      <p class="cap">Passe o mouse para ver os valores de cada dia.</p></div>
    <div class="card" style="margin-top:16px"><h2>Forma (frescor)</h2>
      <p class="hint">Acima de zero = descansado; abaixo = fatigado. A sua vive colada no zero.</p>
      <div class="fig">{charts.form_area(t['load'])}</div></div>"""


def page_workouts(t: dict[str, Any]) -> str:
    rows = []
    zlab = {"easy": "fácil", "mod": "moderado", "hard": "forte", "na": "—"}
    for r in reversed(t["runs"]):
        ate = f'{r["aerobic_training_effect"]:.1f}' if r["aerobic_training_effect"] else "—"
        ana = f'{r["anaerobic_training_effect"]:.1f}' if r.get("anaerobic_training_effect") else "—"
        rows.append(
            f'<tr><td class="mono">{r["start_time_local"][:10]}</td>'
            f'<td class="mono num">{r["km"]}</td><td class="mono num">{r["pace"] or "—"}</td>'
            f'<td class="mono num">{r["avg_hr"] or "—"}/{r["max_hr"] or "—"}</td>'
            f'<td class="mono num">{ate}</td><td class="mono num">{ana}</td>'
            f'<td><span class="pz pz-{r["zone"]}">{zlab[r["zone"]]}</span></td></tr>')
    table = ('<table class="tbl"><thead><tr><th>Data</th><th class="num">km</th>'
             '<th class="num">Ritmo</th><th class="num">FC m/máx</th>'
             '<th class="num" data-tip="Training Effect aeróbico do Garmin (0–5): quanto a '
             'corrida melhorou o condicionamento aeróbico">TE aeró.</th>'
             '<th class="num" data-tip="Training Effect anaeróbico do Garmin (0–5): carga de '
             'alta intensidade / potência da corrida">TE anaer.</th>'
             f'<th>Zona</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return f"""
    <div class="pagehead"><p class="eyebrow">Treinos</p><h1>Volume &amp; intensidade</h1>
      <p class="sub">Cada barra é um período, empilhada por intensidade. Os vazios contam a história.</p></div>
    <div class="card"><div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
      <h2 style="margin:0">Volume por período</h2>
      <div class="seg"><button class="on" data-toggle="vol" data-view="week">Semana</button>
        <button data-toggle="vol" data-view="month">Mês</button></div></div>
      {INTENSITY_LEGEND}
      <div class="fig" data-group="vol" data-view="week">{charts.volume_bars(t['weekly'], 'semana')}</div>
      <div class="fig" data-group="vol" data-view="month" style="display:none">{charts.volume_bars(t['monthly'], 'mês')}</div>
      <p class="cap">Número embaixo de cada barra = treinos no período. Passe o mouse para o detalhe por zona.</p></div>
    <div class="card" style="margin-top:16px"><h2>Todas as corridas</h2>
      <div class="fig" style="overflow-x:auto">{table}</div>
      <p class="cap">TE = Training Effect do Garmin (0–5): impacto aeróbico (resistência) e anaeróbico (alta intensidade) da corrida.</p></div>"""


def page_recovery(t: dict[str, Any]) -> str:
    return f"""
    <div class="pagehead"><p class="eyebrow">Recuperação</p><h1>Como o corpo respondeu</h1>
      <p class="sub">Um alerta real no fim de junho — e uma boa recuperação logo depois.</p></div>
    <div class="card"><h2>FC de repouso</h2>
      <p class="hint">Subiu de 40 para 49 bpm entre 28/jun e 05/jul — sinal de estresse/recuperação ruim.</p>
      <div class="fig">{charts.rhr_line(t['rhr'])}</div></div>
    <div class="card" style="margin-top:16px"><h2>Body Battery &amp; estresse</h2>
      <div class="legend"><span><span class="key" style="background:var(--good)"></span><b>bateria</b> (faixa do dia)</span>
        <span><span class="key" style="background:var(--alert)"></span>dias depletados</span>
        <span><span class="key" style="background:var(--ink-2)"></span>estresse médio</span></div>
      <div class="fig">{charts.bb_range(t['bb'])}</div>
      <p class="cap">Barra = amplitude da bateria no dia. Sono não aparece nos dias sem relógio.</p></div>"""


def page_reports(t: dict[str, Any]) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    files = sorted(REPORTS_DIR.glob("*.json"), reverse=True)
    items = []
    for f in files:
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append(f'<div class="ritem"><div><b>{_esc(meta.get("title", f.stem))}</b>'
                     f'<br><span class="hint">{_esc(meta.get("generated_at", "")[:16])}</span></div>'
                     f'<span class="pill">{_esc(meta.get("km", "—"))} km · CTL {_esc(meta.get("ctl", "—"))}</span></div>')
    archive = "".join(items) or '<p class="empty">Nenhum relatório salvo ainda. Gere o primeiro abaixo — os próximos se acumulam a cada semana.</p>'
    return f"""
    <div class="pagehead"><p class="eyebrow">Relatórios</p><h1>Arquivo</h1>
      <p class="sub">Cada semana vira um instantâneo salvo, navegável no tempo.</p></div>
    <div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
      <h2 style="margin:0">Relatórios salvos</h2>
      <form method="post" action="/relatorios/gerar"><button class="pill" style="border:0;cursor:pointer;background:var(--accent);color:#fff">+ Gerar instantâneo</button></form>
    </div><div class="rlist" style="margin-top:12px">{archive}</div></div>"""


# ---------------------------------------------------------------- shell -------
LOGO = ('<svg viewBox="0 0 100 100" width="22" height="22" fill="#fff" aria-hidden="true">'
        '<polygon points="49.8,43.0 53.8,27.3 48.4,4.0 44.6,27.6"/>'
        '<polygon points="53.3,43.8 61.3,36.3 65.5,20.9 55.0,32.9"/>'
        '<polygon points="56.1,46.5 69.9,43.3 85.5,29.5 65.8,36.1"/>'
        '<polygon points="57.0,49.3 66.0,51.4 78.8,47.0 65.4,45.3"/>'
        '<polygon points="56.2,53.3 67.5,64.3 89.7,71.1 71.7,56.5"/>'
        '<polygon points="53.5,56.1 55.2,65.7 65.0,76.0 61.0,62.4"/>'
        '<polygon points="43.9,46.5 33.0,35.0 11.0,27.5 28.5,42.8"/>'
        '<polygon points="43.0,49.5 31.3,44.8 13.1,47.4 30.8,52.6"/>'
        '<polygon points="43.7,53.1 33.6,54.2 22.1,63.6 36.6,60.3"/>'
        '<polygon points="45.7,55.5 33.4,64.2 23.5,83.9 40.2,69.5"/>'
        '<polygon points="49.5,57.0 45.3,67.1 47.7,82.9 52.3,67.6"/>'
        '<polygon points="53.9,55.8 57.8,68.7 71.8,82.3 64.4,64.2"/>'
        '<polygon points="56.3,53.1 63.2,60.0 77.0,63.2 66.0,54.2"/></svg>')
NAV = [("/", "Visão geral"), ("/carga", "Condição & carga"), ("/treinos", "Treinos"),
       ("/recuperacao", "Recuperação"), ("/relatorios", "Relatórios")]


def shell(active: str, title: str, body: str) -> str:
    nav = "".join(
        f'<a href="{href}" class="{"on" if href == active else ""}"><span class="ic"></span>{label}</a>'
        for href, label in NAV)
    return (
        f"<title>Coach · {title}</title><style>{CSS}{charts.CHART_CSS}</style>"
        '<div class="shell"><aside class="side">'
        '<div class="brand"><span class="mark">' + LOGO + '</span><b>Coach</b></div>'
        f'<nav class="nav">{nav}</nav>'
        '<form method="post" action="/sync" style="margin-top:auto">'
        '<button class="pill" style="border:0;cursor:pointer;width:100%">↻ Sincronizar</button></form>'
        '<div class="foot">Painel local · dados do Garmin</div>'
        f'</aside><main class="main">{body}</main></div>'
        f"<script>{JS}</script>")


def _render(active: str, title: str, page_fn) -> str:
    conn = get_connection()
    try:
        run_migrations(conn)
        t = load_data(conn)
    finally:
        conn.close()
    return shell(active, title, page_fn(t))


# ---------------------------------------------------------------- routes ------
@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return _render("/", "Visão geral", page_overview)


@app.get("/carga", response_class=HTMLResponse)
def carga() -> str:
    return _render("/carga", "Condição & carga", page_load)


@app.get("/treinos", response_class=HTMLResponse)
def treinos() -> str:
    return _render("/treinos", "Treinos", page_workouts)


@app.get("/recuperacao", response_class=HTMLResponse)
def recuperacao() -> str:
    return _render("/recuperacao", "Recuperação", page_recovery)


@app.get("/relatorios", response_class=HTMLResponse)
def relatorios() -> str:
    return _render("/relatorios", "Relatórios", page_reports)


@app.post("/relatorios/gerar")
def gerar_relatorio() -> RedirectResponse:
    conn = get_connection()
    try:
        run_migrations(conn)
        t = load_data(conn)["totals"]
    finally:
        conn.close()
    REPORTS_DIR.mkdir(exist_ok=True)
    today = date.today().isoformat()
    snap = {"title": f"Relatório · semana de {today}", "generated_at": datetime.now().isoformat(),
            "km": t["km"], "ctl": round(t["ctl"], 1), "runs": t["runs"]}
    (REPORTS_DIR / f"{today}.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    return RedirectResponse(url="/relatorios", status_code=303)


@app.post("/sync")
def sync() -> RedirectResponse:
    try:
        from coach.daily import run_daily
        run_daily()
    except Exception:
        pass
    return RedirectResponse(url="/", status_code=303)
