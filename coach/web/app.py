"""Local dashboard (deterministic): view and store Garmin-derived data and reports.

Five screens — Visão geral, Condição & carga, Treinos, Recuperação, Relatórios — served
over coach.db. Server-rendered HTML + inline SVG (charts.py) + one design system
(assets.py). It stores and shows; it makes no training decisions.

Run (offline):  python -m coach.web  ->  http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Request
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
        "SELECT date, resting_hr, sleep_seconds, deep_sleep_seconds, light_sleep_seconds,"
        " rem_sleep_seconds, awake_sleep_seconds, sleep_hr_avg, sleep_hr_min,"
        " body_battery_high, body_battery_low, avg_stress FROM daily_wellness ORDER BY date")]
    races = [dict(r) for r in conn.execute(
        "SELECT date, time_5k_s, time_10k_s, time_half_s, time_marathon_s"
        " FROM race_prediction ORDER BY date")]

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
        end = date.fromisocalendar(2026, n, 7)  # ISO Sunday of week n
        weekly.append({"label": f"Semana {n}", "short": f"S{n}", "n": n,
                       "end": end.isoformat(), "completed": end < date.today(), **v})

    mo = bucketize(lambda d: d.month, None, None)
    monthly = []
    for m in sorted(mo):
        v = mo[m]
        monthly.append({"label": PT_MONTH[m], "short": PT_MONTH_SHORT[m], **v})

    rhr = [w for w in well if w["resting_hr"] is not None][-40:]
    bb = [w for w in well if w["body_battery_high"] is not None][-18:]

    sleep = []
    # only nights with stage data (older nights predate the sleep-detail columns;
    # a backfill fills them in) — avoids empty bars
    for w in [x for x in well if x["deep_sleep_seconds"] is not None][-14:]:
        sd = date.fromisoformat(w["date"])
        deep, light = (w["deep_sleep_seconds"] or 0) / 3600, (w["light_sleep_seconds"] or 0) / 3600
        rem, awake = (w["rem_sleep_seconds"] or 0) / 3600, (w["awake_sleep_seconds"] or 0) / 3600
        sleep.append({
            "label": f"{sd.day:02d}/{sd.month:02d}", "short": f"{sd.day:02d}",
            "deep": deep, "light": light, "rem": rem, "awake": awake,
            "total_h": deep + light + rem + awake,
            "asleep_hm": _fmt_sleep(w["sleep_seconds"]), "deep_hm": _fmt_sleep(w["deep_sleep_seconds"]),
            "light_hm": _fmt_sleep(w["light_sleep_seconds"]), "rem_hm": _fmt_sleep(w["rem_sleep_seconds"]),
            "awake_hm": _fmt_sleep(w["awake_sleep_seconds"]),
            "hr_avg": w["sleep_hr_avg"], "hr_min": w["sleep_hr_min"]})

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
        "rhr": rhr, "bb": bb, "sleep": sleep, "races": races,
        "plan": plan, "pworkouts": pworkouts,
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


def _hms(sec: int | None) -> str:
    if not sec:
        return "—"
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _race_tile(lbl: str, key: str, races: list[dict[str, Any]]) -> str:
    vals = [x[key] for x in races if x[key]]
    delta = ""
    if len(vals) >= 2:
        d = vals[-1] - vals[-2]  # negative = faster than the previous snapshot
        if d < 0:
            delta = f'<span class="rt-dn good">▼ {_hms(-d)}</span>'
        elif d > 0:
            delta = f'<span class="rt-dn warn">▲ {_hms(d)}</span>'
        else:
            delta = '<span class="rt-dn muted">=</span>'
    return (f'<div class="rtile"><div class="rt-d">{lbl}</div>'
            f'<div class="rt-t mono">{_hms(races[-1][key])}</div>'
            f'{delta}{charts.race_spark(vals)}</div>')


def _race_card(races: list[dict[str, Any]]) -> str:
    if not races:
        return ('<div class="card"><h2>Previsões de prova</h2>'
                '<p class="empty">Ainda sem previsão — aparece após o próximo sync.</p></div>')
    tiles = "".join(_race_tile(lbl, key, races) for lbl, key in (
        ("5 km", "time_5k_s"), ("10 km", "time_10k_s"),
        ("21 km", "time_half_s"), ("42 km", "time_marathon_s")))
    trend = ("<p class=\"hint\">Tempos que o Garmin projeta hoje — a linha mostra a evolução."
             "</p>" if len(races) >= 2 else
             "<p class=\"hint\">Tempos que o Garmin projeta hoje — a tendência aparece conforme"
             " os dias acumulam.</p>")
    return (f'<div class="card"><h2>Previsões de prova</h2>{trend}'
            f'<div class="rgrid">{tiles}</div></div>')


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
    <div style="margin-top:16px">{_race_card(t['races'])}</div>
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


ZONE_PT = {"easy": "fácil", "mod": "moderado", "hard": "forte", "na": "corrida"}


def _kpi(label: str, val: str, sub: str) -> str:
    return (f'<div class="card synccard"><div class="sc-label">{label}</div>'
            f'<div class="sc-row"><span class="sc-val">{val}</span></div>'
            f'<div class="sc-sub">{sub}</div></div>')


def _workouts_kpis(weekly: list[dict[str, Any]]) -> str:
    active = [w for w in weekly if w["runs"] > 0]
    completed = [w for w in active if w["completed"]]
    last = max(completed, key=lambda w: w["n"], default=None)
    avg_km = sum(w["km"] for w in active) / len(active) if active else 0.0
    avg_runs = sum(w["runs"] for w in active) / len(active) if active else 0.0
    best = max(active, key=lambda w: w["km"], default=None)
    if last:
        d = last["km"] - avg_km
        delta = f'{d:+.0f} km vs média' if abs(d) >= 0.5 else 'na média'
        vol = _kpi("Volume · última semana", f'{last["km"]:.0f} km', f'Semana {last["n"]} · {delta}')
        wk = _kpi("Treinos · última semana", f'{last["runs"]}', f'média {avg_runs:.1f}/semana')
    else:
        vol = _kpi("Volume · última semana", "—", "sem semana concluída")
        wk = _kpi("Treinos · última semana", "—", "—")
    avg = _kpi("Média por semana", f'{avg_km:.0f} km', f'sobre {len(active)} semanas ativas')
    top = (_kpi("Semana mais forte", f'{best["km"]:.0f} km', f'Semana {best["n"]}')
           if best else _kpi("Semana mais forte", "—", "—"))
    return f'<div class="grid g4">{vol}{wk}{avg}{top}</div>'


def _week_strip(runs: list[dict[str, Any]], week: int) -> str:
    start, end = date.fromisocalendar(2026, week, 1), date.fromisocalendar(2026, week, 7)
    header = f'Semana {week} · {_dm(start)}–{_dm(end)}'
    if end >= date.today():
        return (f'<div class="card" id="semana" style="margin-top:16px"><h2>{header}</h2>'
                '<p class="empty">Semana em andamento — o resumo dos 7 dias abre quando ela '
                'fechar (domingo).</p></div>')
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        if start <= r["d"] <= end:
            by_day.setdefault(r["d"].isoformat(), []).append(r)
    cells = []
    for i in range(7):
        dd = date.fromisocalendar(2026, week, i + 1)
        wd = PT_WD[dd.weekday()]
        day_runs = by_day.get(dd.isoformat(), [])
        if day_runs:
            r = max(day_runs, key=lambda x: x["km"])  # the day's main run
            extra = f' +{len(day_runs)-1}' if len(day_runs) > 1 else ''
            cells.append(
                f'<div class="pday run done"><div class="pd">{wd} {dd.day:02d}</div>'
                f'<div class="pt">{ZONE_PT.get(r["zone"], "corrida")}{extra}</div>'
                f'<div class="pk">{r["km"]:.0f} km · {r["pace"] or "—"}</div></div>')
        else:
            cells.append(
                f'<div class="pday rest"><div class="pd">{wd} {dd.day:02d}</div>'
                '<div class="pt">descanso</div><div class="pk">&mdash;</div></div>')
    return (f'<div class="card" id="semana" style="margin-top:16px"><h2>{header}</h2>'
            '<p class="hint">O que você fez em cada dia — clique noutra semana no gráfico acima.</p>'
            f'<div class="plan" style="margin-top:10px">{"".join(cells)}</div></div>')


def page_workouts(t: dict[str, Any], week: str | None = None) -> str:
    completed_ns = [w["n"] for w in t["weekly"] if w["completed"]]
    sel = int(week) if (week or "").isdigit() and int(week) in completed_ns else None
    if sel is None:
        with_runs = [w["n"] for w in t["weekly"] if w["completed"] and w["runs"] > 0]
        sel = max(with_runs, default=(max(completed_ns, default=None)))

    rows = []
    for r in reversed(t["runs"]):
        ate = f'{r["aerobic_training_effect"]:.1f}' if r["aerobic_training_effect"] else "—"
        ana = f'{r["anaerobic_training_effect"]:.1f}' if r.get("anaerobic_training_effect") else "—"
        rows.append(
            f'<tr><td class="mono">{r["start_time_local"][:10]}</td>'
            f'<td class="mono num">{r["km"]}</td><td class="mono num">{r["pace"] or "—"}</td>'
            f'<td class="mono num">{r["avg_hr"] or "—"}/{r["max_hr"] or "—"}</td>'
            f'<td class="mono num">{ate}</td><td class="mono num">{ana}</td>'
            f'<td><span class="pz pz-{r["zone"]}">{ZONE_PT[r["zone"]] if r["zone"] != "na" else "—"}</span></td></tr>')
    table = ('<table class="tbl"><thead><tr><th>Data</th><th class="num">km</th>'
             '<th class="num">Ritmo</th><th class="num">FC m/máx</th>'
             '<th class="num" data-tip="Training Effect aeróbico do Garmin (0–5): quanto a '
             'corrida melhorou o condicionamento aeróbico">TE aeró.</th>'
             '<th class="num" data-tip="Training Effect anaeróbico do Garmin (0–5): carga de '
             'alta intensidade / potência da corrida">TE anaer.</th>'
             f'<th>Zona</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    zoom = _week_strip(t["runs"], sel) if sel else ""
    return f"""
    <div class="pagehead"><p class="eyebrow">Treinos</p><h1>Volume &amp; intensidade</h1>
      <p class="sub">Volume por semana em primeiro plano — clique numa semana concluída para ver os 7 dias.</p></div>
    {_workouts_kpis(t['weekly'])}
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
      <h2 style="margin:0">Volume por semana</h2>
      <div class="seg"><button class="on" data-toggle="vol" data-view="week">Semana</button>
        <button data-toggle="vol" data-view="month">Mês</button></div></div>
      {INTENSITY_LEGEND}
      <div class="fig" data-group="vol" data-view="week">{charts.volume_bars(t['weekly'], 'semana', pips=True, week_link=True, selected=sel)}</div>
      <div class="fig" data-group="vol" data-view="month" style="display:none">{charts.volume_bars(t['monthly'], 'mês')}</div>
      <p class="cap">Pontos embaixo = treinos na semana · linha tracejada = média · clique numa semana para ampliar.</p></div>
    {zoom}
    <details class="allruns" style="margin-top:16px"><summary>Ver todas as corridas ({len(t['runs'])})</summary>
      <div class="fig" style="overflow-x:auto;margin-top:12px">{table}</div>
      <p class="cap">TE = Training Effect do Garmin (0–5): impacto aeróbico (resistência) e anaeróbico (alta intensidade).</p></details>"""


def _sleep_panel_html(n: dict[str, Any]) -> str:
    def row(cls: str, k: str, v: str) -> str:
        return (f'<div class="sd-row"><span class="sd-k"><i class="sw {cls}"></i>{k}</span>'
                f'<span class="sd-v">{_esc(v)}</span></div>')
    return (f'<div class="sd-date">{_esc(n["label"])}</div>'
            f'<div class="sd-total">dormiu <b>{_esc(n["asleep_hm"])}</b></div>'
            '<div class="sd-rows">'
            + row("s-deep", "profundo", n["deep_hm"]) + row("s-light", "leve", n["light_hm"])
            + row("s-rem", "REM", n["rem_hm"]) + row("s-awake", "acordado", n["awake_hm"])
            + '</div>'
            f'<div class="sd-hr">FC noturna <b>{n["hr_avg"] or "—"}</b> bpm '
            f'<span class="sd-min">(mín {n["hr_min"] or "—"})</span></div>')


def page_recovery(t: dict[str, Any]) -> str:
    nights = t["sleep"]
    if nights:
        sleep_body = (
            '<div class="sleep-wrap">'
            f'<div class="fig">{charts.sleep_stages(nights)}</div>'
            f'<div class="sleep-detail" id="sleep-detail">{_sleep_panel_html(nights[-1])}</div>'
            '</div>'
            '<p class="cap">Passe o mouse numa noite para ver o detalhe ao lado. '
            'Noites sem o relógio não aparecem.</p>')
    else:
        sleep_body = charts.sleep_stages(nights)
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
      <p class="cap">Barra = amplitude da bateria no dia. Sono não aparece nos dias sem relógio.</p></div>
    <div class="card" style="margin-top:16px"><h2>Sono — fases &amp; FC noturna</h2>
      <div class="legend">
        <span><span class="key" style="background:var(--seq-4)"></span><b>profundo</b></span>
        <span><span class="key" style="background:var(--seq-2)"></span><b>leve</b></span>
        <span><span class="key" style="background:var(--teal)"></span><b>REM</b></span>
        <span><span class="key" style="background:var(--muted)"></span>acordado</span></div>
      {sleep_body}</div>"""


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


# ------------------------------------------------------------- sync page ------
_MONTHS_ABBR = ["", "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago",
                "set", "out", "nov", "dez"]


def _dm(d: date) -> str:
    return f"{d.day:02d} {_MONTHS_ABBR[d.month]}"


def _fmt_dt(iso: str | None) -> str:
    """ISO UTC timestamp -> local 'DD/mon HH:MM'."""
    try:
        t = datetime.fromisoformat(iso).astimezone()  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return "—"
    return f"{t.day:02d}/{_MONTHS_ABBR[t.month]} {t.strftime('%H:%M')}"


def _fmt_sleep(secs: int | None) -> str:
    if not secs:
        return "—"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}"


def _ago(iso: str | None) -> str:
    """Portuguese relative time from an ISO timestamp (e.g. 'há 2 h')."""
    if not iso:
        return "nunca"
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return "—"
    secs = max(0.0, (datetime.now(t.tzinfo) - t).total_seconds())
    if secs < 90:
        return "agora"
    if secs < 5400:  # 90 min
        return f"há {int(round(secs / 60))} min"
    if secs < 129600:  # 36 h
        return f"há {int(round(secs / 3600))} h"
    return f"há {int(round(secs / 86400))} dias"


def _hours_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return (datetime.now(t.tzinfo) - t).total_seconds() / 3600


def load_sync_data(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    today = date.today()

    last = conn.execute(
        "SELECT ran_at, status, source, detail, activities_fetched"
        " FROM sync_log ORDER BY id DESC LIMIT 1").fetchone()
    last_ok = conn.execute(
        "SELECT ran_at FROM sync_log WHERE status='ok' ORDER BY id DESC LIMIT 1").fetchone()
    history = [dict(r) for r in conn.execute(
        "SELECT ran_at, status, source, detail FROM sync_log ORDER BY id DESC LIMIT 12")]

    act = conn.execute(
        "SELECT COUNT(*) AS n, MAX(start_time_local) AS last_dt, MAX(synced_at) AS synced"
        " FROM activity WHERE type='running'").fetchone()
    prof = conn.execute("SELECT vo2max, updated_at FROM user_profile WHERE id=1").fetchone()

    span_days = 35
    start = today - timedelta(days=span_days - 1)
    wrows = {r["date"]: dict(r) for r in conn.execute(
        "SELECT * FROM daily_wellness WHERE date >= ? ORDER BY date", (start.isoformat(),))}
    w_last = conn.execute(
        "SELECT MAX(date) AS d, MAX(synced_at) AS synced FROM daily_wellness").fetchone()
    core_cols = ("resting_hr", "sleep_seconds", "body_battery_high", "avg_stress")
    coverage, missing = [], 0
    for i in range(span_days - 1, -1, -1):
        dd = today - timedelta(days=i)
        r = wrows.get(dd.isoformat())
        if r is None or all(r[c] is None for c in core_cols):
            state = "missing"  # no row, or an empty placeholder (today not yet finalized)
            missing += 1
        elif r["resting_hr"] is not None and r["sleep_seconds"] is not None:
            state = "full"  # both night signals present
        else:
            state = "partial"  # some data, a signal missing (usually sleep)
        coverage.append({"date": dd, "state": state, "row": r})

    return {
        "last": dict(last) if last else None,
        "last_ok": last_ok["ran_at"] if last_ok else None,
        "history": history,
        "activities": {"n": act["n"], "last_dt": act["last_dt"], "synced": act["synced"]},
        "vo2": {"value": prof["vo2max"] if prof else None,
                "updated": prof["updated_at"] if prof else None},
        "wellness": {"last": w_last["d"] if w_last else None,
                     "synced": w_last["synced"] if w_last else None,
                     "coverage": coverage, "missing": missing, "span": span_days},
    }


def _sync_header(d: dict[str, Any]) -> str:
    last, last_ok = d["last"], d["last_ok"]
    if not last:
        cls, chip, head = "warn", "sem sync", "Nunca sincronizado"
    elif last["status"] == "failed":
        cls, chip, head = "alert", "falha", f"Falha no último sync · {_ago(last['ran_at'])}"
    else:
        h = _hours_since(last_ok)
        if h is not None and h < 24:
            cls, chip, head = "good", "atualizado", f"Sincronizado {_ago(last_ok)}"
        elif h is not None and h < 72:
            cls, chip, head = "warn", "defasado", f"Último sync {_ago(last_ok)}"
        else:
            cls, chip, head = "warn", "desatualizado", f"Último sync {_ago(last_ok)}"
    dot = {"good": "var(--good)", "alert": "var(--alert)", "warn": "var(--warn)"}[cls]
    backfill = "".join(
        f'<form method="post" action="/sync?next=/sincronizacao&amp;wbf={n}"'
        f' data-sync style="display:inline"><button class="pill ghost"'
        f' style="cursor:pointer">{n}d</button></form>'
        for n in (7, 14, 30, 60))
    actions = (
        '<form method="post" action="/sync?next=/sincronizacao" data-sync style="display:inline">'
        '<button class="pill solid" style="border:0;cursor:pointer"'
        ' data-progress="↻ Sincronizando…">↻ Sincronizar agora</button></form>'
        '<span class="bf" data-tip="Re-busca o bem-estar (inclui as fases do sono) do zero,'
        ' pelo período escolhido, para preencher lacunas">'
        '<span class="bf-lbl">Re-buscar:</span>' + backfill + '</span>')
    return (f'<div class="card"><div class="syncstate">'
            f'<div class="ss-head"><span class="ss-dot" style="background:{dot}"></span>'
            f'<div><div class="sc-label">Estado</div><h2>{_esc(head)}</h2></div>'
            f'<span class="gtag t-{cls}">{chip}</span></div>'
            f'<div class="ss-actions">{actions}</div></div></div>')


def _domain_card(label: str, val: str, sub: str, tag: str | None = None,
                 tagcls: str = "accent") -> str:
    chip = f'<span class="gtag t-{tagcls}">{_esc(tag)}</span>' if tag else ""
    return (f'<div class="card synccard"><div class="sc-label">{_esc(label)}</div>'
            f'<div class="sc-row"><span class="sc-val">{_esc(val)}</span>{chip}</div>'
            f'<div class="sc-sub">{sub}</div></div>')


def _wellness_heatmap(cov: list[dict[str, Any]]) -> str:
    cells = [f'<span class="hm-wd">{w}</span>'
             for w in ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]]
    cells += ['<span class="hm-cell hm-pad"></span>'] * cov[0]["date"].weekday()
    for c in cov:
        r = c["row"]
        if c["state"] == "missing" or not r:
            detail = "sem dados"
        else:
            detail = (f'FC {r["resting_hr"] or "—"} · sono {_fmt_sleep(r["sleep_seconds"])}'
                      f' · bateria {r["body_battery_high"] or "—"}→{r["body_battery_low"] or "—"}')
        tip = f'{_dm(c["date"])} — {detail}'
        cells.append(f'<span class="hm-cell hm-{c["state"]}" data-tip="{_esc(tip)}"></span>')
    legend = ('<div class="legend" style="margin-top:12px">'
              '<span><span class="key" style="background:var(--seq-3)"></span><b>completo</b></span>'
              '<span><span class="key" style="background:var(--seq-1)"></span><b>parcial</b></span>'
              '<span><span class="key" style="background:var(--surface-2);'
              'border:1px solid var(--line)"></span>faltando</span></div>')
    return f'<div class="hm">{"".join(cells)}</div>{legend}'


_DETAIL_OK = re.compile(r"^(\d+) activities, (\d+) wellness days?$")


def _pt_detail(detail: str | None) -> str:
    """Localize the sync_log detail. The two numbers are different domains: activities
    are the whole 120-day window; wellness is only the days re-fetched this run."""
    if not detail:
        return "—"
    m = _DETAIL_OK.match(detail)
    if m:
        return f"{m.group(1)} atividades (120d) · {m.group(2)} dias de bem-estar"
    return (detail.replace("login ok, 0 activities returned", "conectado, 0 atividades")
                  .replace("activities", "atividades"))


def _sync_history(history: list[dict[str, Any]]) -> str:
    if not history:
        return '<p class="empty">Nenhuma sincronização registrada ainda.</p>'
    st = {"ok": ("ok", "good"), "failed": ("falha", "alert"), "suspicious": ("sem dados", "warn")}
    src = {"garmin_sync": "Garmin", "manual_import": "importação"}
    rows = []
    for h in history:
        lbl, cls = st.get(h["status"], (h["status"], "accent"))
        rows.append(
            f'<tr><td class="mono">{_fmt_dt(h["ran_at"])}</td>'
            f'<td><span class="gtag t-{cls}">{lbl}</span></td>'
            f'<td>{_esc(src.get(h["source"], h["source"]))}</td>'
            f'<td>{_esc(_pt_detail(h["detail"]))}</td></tr>')
    return ('<table class="tbl"><thead><tr><th>Quando</th><th>Status</th><th>Fonte</th>'
            f'<th>Detalhe</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def page_sync(d: dict[str, Any]) -> str:
    a = d["activities"]
    act_last = date.fromisoformat(a["last_dt"][:10]) if a["last_dt"] else None
    act_card = _domain_card(
        "Atividades", _dm(act_last) if act_last else "—",
        f'{a["n"]} corridas na janela · sync {_ago(a["synced"])}')

    w = d["wellness"]
    w_last = date.fromisoformat(w["last"]) if w["last"] else None
    w_tag = f'{w["missing"]} sem dado' if w["missing"] else None
    well_card = _domain_card(
        "Wellness", _dm(w_last) if w_last else "—",
        f'último dia · {w["missing"]} faltando em {w["span"]}d · sync {_ago(w["synced"])}',
        tag=w_tag, tagcls="warn")

    v = d["vo2"]
    vo2_card = _domain_card(
        "VO₂max", f'{v["value"]:.0f}' if v["value"] else "—", f'lido {_ago(v["updated"])}')

    return f"""
    <div class="pagehead"><p class="eyebrow">Dados</p><h1>Sincronização</h1>
      <p class="sub">O que já entrou do Garmin e o que ainda falta — atividades, bem-estar e VO₂max.</p></div>
    {_sync_header(d)}
    <div class="grid g3" style="margin-top:16px">{act_card}{well_card}{vo2_card}</div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h2>Cobertura de bem-estar</h2>
        <p class="hint">Últimos {w['span']} dias — passe o mouse em cada dia.</p>
        {_wellness_heatmap(w['coverage'])}</div>
      <div class="card"><h2>Histórico</h2>
        <p class="hint">Últimas sincronizações e seu resultado.</p>
        <div class="fig" style="overflow-x:auto">{_sync_history(d['history'])}</div></div>
    </div>
    <div class="card" style="margin-top:16px"><h2>Exportar para o coach</h2>
      <p class="hint">Gera o snapshot (estado + histórico recente + zonas, cadência, decoupling)
        para colar no chat do coach.</p>
      <a class="pill solid" style="display:inline-block;margin-top:10px;text-decoration:none;
        border:0" href="/exportar">Gerar snapshot →</a></div>"""


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
       ("/recuperacao", "Recuperação"), ("/relatorios", "Relatórios"),
       ("/sincronizacao", "Sincronização")]


def shell(active: str, title: str, body: str) -> str:
    nav = "".join(
        f'<a href="{href}" class="{"on" if href == active else ""}"><span class="ic"></span>{label}</a>'
        for href, label in NAV)
    return (
        f"<title>Coach · {title}</title><style>{CSS}{charts.CHART_CSS}</style>"
        '<div class="shell"><aside class="side">'
        '<div class="brand"><span class="mark">' + LOGO + '</span><b>Coach</b></div>'
        f'<nav class="nav">{nav}</nav>'
        '<div class="foot">Painel local · dados do Garmin</div>'
        f'</aside><main class="main">{body}</main></div>'
        f"<script>{JS}</script>")


def _render(active: str, title: str, page_fn, banner: str = "") -> str:
    conn = get_connection()
    try:
        run_migrations(conn)
        t = load_data(conn)
    finally:
        conn.close()
    return shell(active, title, banner + page_fn(t))


# --------------------------------------------------------------- feedback -----
def _banner(kind: str, title: str, msg: str) -> str:
    """A dismissible status banner (ok / warn / err), shown after a sync."""
    return (f'<div class="banner b-{kind}" role="status">'
            '<span class="bico"></span>'
            f'<div class="btext"><b>{_esc(title)}</b><span>{msg}</span></div>'
            '<button class="bx" type="button" aria-label="Fechar"'
            " onclick=\"this.closest('.banner').remove()\">&times;</button></div>")


def _sync_banner(qp: Any) -> str:
    """Build the post-sync feedback banner from the redirect's query params."""
    s = qp.get("sync")
    if not s:
        return ""
    if s == "ok":
        well = _esc(qp.get("well", "0"))
        alerts = qp.get("alerts", "0")
        extra = f" · {_esc(alerts)} alerta(s) novo(s)" if alerts not in ("0", "", None) else ""
        return _banner("ok", "Sincronizado com o Garmin",
                       f"Bem-estar: {well} dias · condição e alertas recalculados{extra}")
    if s == "warn":
        return _banner("warn", "Conectado, mas sem corridas novas",
                       "O Garmin respondeu sem atividades no período. Tente de novo mais tarde.")
    e = qp.get("e", "")
    detail = f"Detalhe: {_esc(e)}" if e else "Verifique a conexão ou o login do Garmin."
    return _banner("err", "Falha ao sincronizar", detail)


# ---------------------------------------------------------------- routes ------
@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> str:
    return _render("/", "Visão geral", page_overview, _sync_banner(request.query_params))


@app.get("/carga", response_class=HTMLResponse)
def carga() -> str:
    return _render("/carga", "Condição & carga", page_load)


@app.get("/treinos", response_class=HTMLResponse)
def treinos(request: Request) -> str:
    week = request.query_params.get("w")
    conn = get_connection()
    try:
        run_migrations(conn)
        t = load_data(conn)
    finally:
        conn.close()
    return shell("/treinos", "Treinos", page_workouts(t, week))


@app.get("/recuperacao", response_class=HTMLResponse)
def recuperacao() -> str:
    return _render("/recuperacao", "Recuperação", page_recovery)


@app.get("/relatorios", response_class=HTMLResponse)
def relatorios() -> str:
    return _render("/relatorios", "Relatórios", page_reports)


@app.get("/sincronizacao", response_class=HTMLResponse)
def sincronizacao(request: Request) -> str:
    conn = get_connection()
    try:
        run_migrations(conn)
        d = load_sync_data(conn)
    finally:
        conn.close()
    return shell("/sincronizacao", "Sincronização",
                 _sync_banner(request.query_params) + page_sync(d))


@app.get("/exportar", response_class=HTMLResponse)
def exportar() -> str:
    from coach.export import write_snapshot
    path, text = write_snapshot()
    body = (
        '<div class="pagehead"><p class="eyebrow">Export</p><h1>Snapshot para o coach</h1>'
        '<p class="sub">Cole no chat do coach — ou use o arquivo salvo abaixo.</p></div>'
        '<div class="card"><div style="display:flex;justify-content:space-between;'
        'align-items:center;gap:8px;flex-wrap:wrap"><h2 style="margin:0">snapshot.txt</h2>'
        '<button class="pill solid" style="border:0;cursor:pointer" '
        "onclick=\"navigator.clipboard.writeText(document.getElementById('snap').value);"
        "this.textContent='Copiado!'\">Copiar tudo</button></div>"
        f'<p class="hint">Salvo em: {_esc(str(path))}</p>'
        '<textarea id="snap" readonly style="width:100%;height:58vh;margin-top:10px;'
        'font-family:ui-monospace,monospace;font-size:12.5px;background:var(--inset);'
        'color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:12px;'
        f'resize:vertical">{_esc(text)}</textarea>'
        '<p class="cap"><a href="/sincronizacao" style="color:var(--accent)">← voltar</a></p></div>')
    return shell("/sincronizacao", "Export", body)


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
def sync(request: Request) -> RedirectResponse:
    from coach.daily import run_daily
    qp = request.query_params
    nxt = qp.get("next")
    base = nxt if nxt in ("/", "/sincronizacao") else "/"  # whitelist (no open redirect)
    wbf = qp.get("wbf")
    backfill = int(wbf) if wbf in ("7", "14", "30", "60") else None
    try:
        summary = run_daily(wellness_backfill=backfill)
    except Exception as exc:  # a recompute stage failed unexpectedly
        return RedirectResponse(
            url=base + "?" + urlencode({"sync": "err", "e": type(exc).__name__}),
            status_code=303)
    s = summary.get("sync") or {}
    status = s.get("status")
    if status == "ok":
        params = {"sync": "ok", "well": s.get("wellness_days", 0),
                  "alerts": summary.get("alerts_new", 0)}
    elif status == "suspicious":
        params = {"sync": "warn"}
    else:  # "failed"
        params = {"sync": "err", "e": (s.get("error") or "")[:80]}
    return RedirectResponse(url=base + "?" + urlencode(params), status_code=303)
