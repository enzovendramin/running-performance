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
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, Request
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

    # cross-training (non-running) — shown as a labeled "day off" in the week zoom
    cross = [dict(r) for r in conn.execute(
        "SELECT start_time_local, type, distance_m, duration_s FROM activity"
        " WHERE type != 'running' ORDER BY start_time_local")]
    for a in cross:
        a["d"] = date.fromisoformat(a["start_time_local"][:10])
        a["km"] = (a["distance_m"] or 0) / 1000
        a["min"] = round((a["duration_s"] or 0) / 60)

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
    iso_year, cur_wk = date.today().isocalendar()[0], date.today().isocalendar()[1]
    first_wk = min((a["d"].isocalendar()[1] for a in acts
                    if a["d"].isocalendar()[0] == iso_year), default=cur_wk)
    weekly = []
    for n in range(max(first_wk, cur_wk - 19), cur_wk + 1):  # rolling window up to this week
        v = wk.get(n, {"km": 0.0, "runs": 0, "easy": 0.0, "mod": 0.0, "hard": 0.0})
        seg = date.fromisocalendar(iso_year, n, 1)   # ISO Monday
        end = date.fromisocalendar(iso_year, n, 7)   # ISO Sunday
        rng = (f"{seg.day}–{end.day} {_MONTHS_ABBR[end.month]}" if seg.month == end.month
               else f"{seg.day} {_MONTHS_ABBR[seg.month]}–{end.day} {_MONTHS_ABBR[end.month]}")
        weekly.append({"label": f"Semana {n} · {rng}", "short": f"{seg.day}/{seg.month}",
                       "range": rng, "n": n, "end": end.isoformat(),
                       "completed": end < date.today(), **v})

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
        "rhr": rhr, "bb": bb, "sleep": sleep, "races": races, "cross": cross,
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
           "intervals": "tiros", "hard": "forte", "recovery": "regenerativo",
           "threshold": "limiar", "fartlek": "fartlek", "hills": "ladeira", "race": "prova",
           "off": "folga", "strength": "força", "mobility": "mobilidade", "cross": "cross"}


def _plan_cat(typ: str) -> str:
    if typ in ("rest", "off"):
        return "rest"
    if typ in ("strength", "mobility", "cross"):
        return "support"
    if typ in ("tempo", "intervals", "threshold", "repetitions", "fartlek", "hard", "race", "hills"):
        return "quality"
    return "run"


def _fmt_pace_range(lo: float | None, hi: float | None) -> str | None:
    def mmss(s: float) -> str:
        s = int(s)
        return f"{s // 60}:{s % 60:02d}"
    if lo and hi:
        return f"{mmss(lo)}–{mmss(hi)}"
    return mmss(lo or hi) if (lo or hi) else None


def _plan_metric(km: float | None, pace: str | None, hr: str | None) -> str:
    """Labeled, divider-separated metric units (HTML) — scannable at a glance."""
    def unit(label: str, value: str) -> str:
        lbl = f'<span class="wk-mk">{label}</span>' if label else ""
        return f'<span class="wk-m">{lbl}{value}</span>'
    units = []
    if km:
        units.append(unit("", f'{("%g" % km).replace(".", ",")} km'))
    if pace:
        units.append(unit("ritmo", _esc(pace)))
    if hr:
        units.append(unit("FC", _esc(hr)))
    return "".join(units)


def _plan_detail(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<p class="empty">Nenhum plano ativo. Monte um na conversa com o coach.</p>'
    out = []
    for r in rows:
        done = '<span class="wk-done">✓ feito</span>' if r.get("done") else ""
        metric = f'<div class="wk-metric">{r["metric"]}</div>' if r.get("metric") else ""
        note = f'<div class="wk-note">{_esc(r["note"])}</div>' if r.get("note") else ""
        out.append(
            f'<div class="wk-row"><div class="wk-day">{_esc(r["wd"])} {r["dd"]:02d}</div>'
            f'<div class="wk-body"><div class="wk-head">'
            f'<span class="wk-badge wk-{r["cat"]}">{_esc(r["label"])}</span>{done}</div>'
            f'{metric}{note}</div></div>')
    return '<div class="wk-list">' + "".join(out) + "</div>"


def _plan_strip(t: dict[str, Any]) -> str:
    if not t["plan"] or not t["pworkouts"]:
        return '<p class="empty">Nenhum plano ativo. Monte um na conversa com o coach.</p>'
    rows = []
    for w in t["pworkouts"]:
        d = date.fromisoformat(w["date"])
        cat = _plan_cat(w["type"])
        km = w["target_distance_m"] / 1000 if w["target_distance_m"] else None
        rows.append({
            "wd": PT_WD[d.weekday()], "dd": d.day, "cat": cat,
            "label": PT_TYPE.get(w["type"], w["type"]),
            "metric": _plan_metric(km, _fmt_pace_range(
                w["target_pace_low_s_km"], w["target_pace_high_s_km"]), w["target_intensity"]),
            "note": w["description"], "done": w["done"] and cat in ("run", "quality")})
    return _plan_detail(rows)


_TYPE_EMOJI = {"strength": "💪", "mobility": "🧘", "cross": "🚴", "rest": "😴", "off": "😴"}


def _whatsapp_plan_text(plan: dict[str, Any], pworkouts: list[dict[str, Any]]) -> str:
    """Format the active plan as a WhatsApp-friendly message (plain text + *bold* + emoji)."""
    ws = date.fromisoformat(plan["week_start_date"])
    we = ws + timedelta(days=6)
    rng = (f"{ws.day}–{we.day} {_MONTHS_ABBR[we.month]}" if ws.month == we.month
           else f"{ws.day} {_MONTHS_ABBR[ws.month]}–{we.day} {_MONTHS_ABBR[we.month]}")
    lines = [f"🗓️ *Semana {rng}*"]
    if plan.get("rationale"):
        lines.append(f"_{plan['rationale']}_")
    lines.append("")
    n_run, total_km, n_forca = 0, 0.0, 0
    for w in pworkouts:
        d = date.fromisoformat(w["date"])
        cat = _plan_cat(w["type"])
        emoji = _TYPE_EMOJI.get(w["type"]) or ("🔥" if cat == "quality" else "🏃")
        head = f"*{PT_WD[d.weekday()]} {d.day:02d}* {emoji} {PT_TYPE.get(w['type'], w['type']).capitalize()}"
        km = w["target_distance_m"] / 1000 if w["target_distance_m"] else None
        if km:
            head += f" · {('%g' % km).replace('.', ',')} km"
            total_km += km
        if cat in ("run", "quality"):
            n_run += 1
        if w["type"] == "strength":
            n_forca += 1
        lines.append(head)
        pace = _fmt_pace_range(w["target_pace_low_s_km"], w["target_pace_high_s_km"])
        metric = ([f"ritmo {pace}"] if pace else []) + \
                 ([f"FC {w['target_intensity']}"] if w["target_intensity"] else [])
        if metric:
            lines.append(" · ".join(metric))
        if w["description"]:
            lines.append(f"_{w['description']}_")
        lines.append("")
    footer = [f"{n_run} corrida(s)"]
    if total_km:
        footer.append(f"{('%g' % round(total_km, 1)).replace('.', ',')} km")
    if n_forca:
        footer.append(f"{n_forca}× força")
    lines += ["━━━━━━━━━━", "📊 " + " · ".join(footer)]
    return "\n".join(lines)


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
    wa_btn = ""
    if t["plan"] and t["pworkouts"]:
        watext = _whatsapp_plan_text(dict(t["plan"]), t["pworkouts"])
        wa_btn = (
            f'<textarea id="waText" hidden>{_esc(watext)}</textarea>'
            '<div style="display:flex;gap:8px;flex-wrap:wrap">'
            '<a class="pill" style="background:#25D366;color:#fff;text-decoration:none;border:0"'
            f' target="_blank" rel="noopener" href="https://wa.me/?text={quote(watext)}">'
            'Enviar no WhatsApp</a>'
            '<button class="pill ghost" style="cursor:pointer" onclick="'
            "navigator.clipboard.writeText(document.getElementById('waText').value);"
            "this.textContent='Copiado!'\">Copiar mensagem</button></div>")
    return f"""
    <div class="pagehead"><p class="eyebrow">Painel</p><h1>Visão geral</h1>
      <p class="sub">Motor de elite, base a reconstruir. O essencial num relance — passe o mouse nos gráficos.</p></div>
    <div class="grid g4">
      <div class="card">{g['ctl']}</div><div class="card">{g['tsb']}</div>
      <div class="card">{g['week']}</div><div class="card">{g['vo2']}</div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card span2">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
          <div><h2 style="margin:0">Esta semana</h2>
            <p class="hint" style="margin-top:2px">Plano ativo — ✓ = já feito.</p></div>
          {wa_btn}</div>
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
    <div class="pagehead"><p class="eyebrow">Fisiologia</p><h1>Carga &amp; recuperação</h1>
      <p class="sub">Quanto você carregou (condição × fadiga) e como o corpo respondeu
        (FC de repouso, Body Battery, sono).</p></div>
    <div class="card"><h2>Condição × Fadiga · 4 meses</h2>
      <div class="legend"><span><span class="key" style="background:var(--accent)"></span><b>Condição</b> (fitness, 42d)</span>
        <span><span class="key" style="background:var(--mod)"></span><b>Fadiga</b> (7d)</span></div>
      <div class="fig">{charts.load_lines(t['load'])}</div>
      <p class="cap">Passe o mouse para ver os valores de cada dia.</p></div>
    <div class="card" style="margin-top:16px"><h2>Forma (frescor)</h2>
      <p class="hint">Acima de zero = descansado; abaixo = fatigado (Condição − Fadiga).</p>
      <div class="fig">{charts.form_area(t['load'])}</div></div>
    <div class="card" style="margin-top:16px"><h2>FC de repouso</h2>
      <p class="hint">Tendência da FC de repouso — subidas sustentadas sinalizam estresse ou recuperação ruim.</p>
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
        vol = _kpi("Volume · última semana", f'{last["km"]:.0f} km', f'{last["range"]} · {delta}')
        wk = _kpi("Treinos · última semana", f'{last["runs"]}', f'média {avg_runs:.1f}/semana')
    else:
        vol = _kpi("Volume · última semana", "—", "sem semana concluída")
        wk = _kpi("Treinos · última semana", "—", "—")
    avg = _kpi("Média por semana", f'{avg_km:.0f} km', f'sobre {len(active)} semanas ativas')
    top = (_kpi("Semana mais forte", f'{best["km"]:.0f} km', best["range"])
           if best else _kpi("Semana mais forte", "—", "—"))
    return f'<div class="grid g4">{vol}{wk}{avg}{top}</div>'


_CROSS_PT = {"cycling": "bike", "indoor_cycling": "bike", "lap_swimming": "natação",
             "open_water_swimming": "natação", "strength_training": "força",
             "walking": "caminhada", "hiking": "caminhada"}


def _week_strip(runs: list[dict[str, Any]], cross: list[dict[str, Any]], week: int) -> str:
    start, end = date.fromisocalendar(2026, week, 1), date.fromisocalendar(2026, week, 7)
    today = date.today()
    tag = " · em andamento" if end >= today else ""
    header = f'Semana {week} · {_dm(start)}–{_dm(end)}{tag}'
    if start > today:
        return (f'<div class="card" id="semana" style="margin-top:16px"><h2>{header}</h2>'
                '<p class="empty">Esta semana ainda não começou.</p></div>')
    runs_by, cross_by = {}, {}
    for r in runs:
        if start <= r["d"] <= end:
            runs_by.setdefault(r["d"].isoformat(), []).append(r)
    for a in cross:
        if start <= a["d"] <= end:
            cross_by.setdefault(a["d"].isoformat(), []).append(a)
    cells = []
    for i in range(7):
        dd = date.fromisocalendar(2026, week, i + 1)
        wd, key = PT_WD[dd.weekday()], dd.isoformat()
        if dd > today:  # future day of the current week
            cells.append(
                f'<div class="pday future"><div class="pd">{wd} {dd.day:02d}</div>'
                '<div class="pt">—</div><div class="pk">a fazer</div></div>')
            continue
        day_runs, day_cross = runs_by.get(key, []), cross_by.get(key, [])
        if day_runs:
            r = max(day_runs, key=lambda x: x["km"])  # the day's main run
            xtra = " +bike" if day_cross else (f' +{len(day_runs)-1}' if len(day_runs) > 1 else '')
            cells.append(
                f'<div class="pday run done"><div class="pd">{wd} {dd.day:02d}</div>'
                f'<div class="pt">{ZONE_PT.get(r["zone"], "corrida")}{xtra}</div>'
                f'<div class="pk">{r["km"]:.0f} km · {r["pace"] or "—"}</div></div>')
        elif day_cross:  # no run, but cross-trained — a labeled day off
            a = max(day_cross, key=lambda x: (x["km"], x["min"]))
            metric = f'{a["km"]:.0f} km' if a["km"] else f'{a["min"]} min'
            cells.append(
                f'<div class="pday cross"><div class="pd">{wd} {dd.day:02d}</div>'
                f'<div class="pt">{_CROSS_PT.get(a["type"], a["type"])}</div>'
                f'<div class="pk">{metric}</div></div>')
        else:
            cells.append(
                f'<div class="pday rest"><div class="pd">{wd} {dd.day:02d}</div>'
                '<div class="pt">descanso</div><div class="pk">&mdash;</div></div>')
    return (f'<div class="card" id="semana" style="margin-top:16px"><h2>{header}</h2>'
            '<p class="hint">Corrida (roxo) · cross/força (teal, folga da corrida) · descanso. '
            'Clique noutra semana no gráfico acima.</p>'
            f'<div class="plan" style="margin-top:10px">{"".join(cells)}</div></div>')


_MONTH_FULL = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
               "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def _month_weeks(runs: list[dict[str, Any]], y: int, m: int) -> list[dict[str, Any]]:
    """Weekly aggregates for the ISO weeks that overlap the month (y, m)."""
    first = date(y, m, 1)
    last = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    d = date.fromisocalendar(*first.isocalendar()[:2], 1)          # Monday of the first ISO week
    end_monday = date.fromisocalendar(*last.isocalendar()[:2], 1)  # Monday of the last ISO week
    weeks = []
    while d <= end_monday:
        seg, end = d, d + timedelta(days=6)
        wr = [r for r in runs if seg <= r["d"] <= end]
        rng = (f"{seg.day}–{end.day} {_MONTHS_ABBR[end.month]}" if seg.month == end.month
               else f"{seg.day} {_MONTHS_ABBR[seg.month]}–{end.day} {_MONTHS_ABBR[end.month]}")
        short = (f"{seg.day}–{end.day}/{seg.month}" if seg.month == end.month
                 else f"{seg.day}/{seg.month}–{end.day}/{end.month}")
        weeks.append({
            "n": seg.isocalendar()[1], "short": short, "range": rng,
            "label": f"Semana {seg.isocalendar()[1]} · {rng}", "end": end.isoformat(),
            "completed": end < date.today(), "started": seg <= date.today(),
            "km": sum(r["km"] for r in wr), "runs": len(wr),
            "easy": sum(r["km"] for r in wr if r["zone"] == "easy"),
            "mod": sum(r["km"] for r in wr if r["zone"] == "mod"),
            "hard": sum(r["km"] for r in wr if r["zone"] == "hard")})
        d += timedelta(days=7)
    return weeks


def page_workouts(t: dict[str, Any], week: str | None = None, month: str | None = None) -> str:
    runs, today = t["runs"], date.today()
    if month and re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month):
        y, mo = int(month[:4]), int(month[5:7])
    elif week and week.isdigit():
        wd = date.fromisocalendar(today.isocalendar()[0], int(week), 1)
        y, mo = wd.year, wd.month
    else:
        latest = runs[-1]["d"] if runs else today
        y, mo = latest.year, latest.month
    msel = f"{y:04d}-{mo:02d}"
    mweeks = _month_weeks(runs, y, mo)

    in_month = {w["n"] for w in mweeks}
    sel = int(week) if (week or "").isdigit() and int(week) in in_month else None
    if sel is None:  # default to the most recent started week that has runs (incl. the current one)
        sel = max([w["n"] for w in mweeks if w["started"] and w["runs"] > 0], default=None)

    prev_m = (date(y, mo, 1) - timedelta(days=1)).strftime("%Y-%m")
    next_dt = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
    nxt = (f'<a class="mnav" href="/treinos?m={next_dt.strftime("%Y-%m")}">›</a>'
           if next_dt <= date(today.year, today.month, 1) else '<span class="mnav off">›</span>')
    month_nav = (f'<div class="mnav-bar"><a class="mnav" href="/treinos?m={prev_m}">‹</a>'
                 f'<span class="mnav-lbl">{_MONTH_FULL[mo]} {y}</span>{nxt}</div>')
    m_runs = [r for r in runs if r["d"].year == y and r["d"].month == mo]
    m_total = (f'{sum(r["km"] for r in m_runs):.2f} km · {len(m_runs)} treinos '
               f'em {_MONTH_FULL[mo].lower()}')

    rows = []
    for r in reversed(runs):
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
    zoom = (_week_strip(runs, t["cross"], sel) if sel else
            '<div class="card" id="semana" style="margin-top:16px">'
            '<p class="empty">Nenhuma corrida neste mês.</p></div>')
    return f"""
    <div class="pagehead"><p class="eyebrow">Treinos</p><h1>Volume &amp; intensidade</h1>
      <p class="sub">Volume por semana — navegue os meses e clique numa semana para ver os 7 dias.</p></div>
    {_workouts_kpis(t['weekly'])}
    <div class="card" id="vol" style="margin-top:16px">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <div><h2 style="margin:0">Volume por semana</h2>
          <p class="hint" style="margin-top:2px">{m_total}</p></div>{month_nav}</div>
      {INTENSITY_LEGEND}
      <div class="fig">{charts.volume_bars(mweeks, 'semana', pips=True, week_link=True, selected=sel, month=msel)}</div>
      <p class="cap">Pontos embaixo = treinos na semana · clique numa semana para ampliar.</p></div>
    {zoom}
    <details class="allruns" style="margin-top:16px"><summary>Ver todas as corridas ({len(runs)})</summary>
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


def page_reports(t: dict[str, Any]) -> str:
    plan = t["plan"]
    plan_line = (f'Plano ativo: semana de {_esc(plan["week_start_date"])}.' if plan
                 else 'Nenhum plano ativo — importe um abaixo.')
    btn = ('display:inline-block;margin-top:12px;text-decoration:none;border:0')
    return f"""
    <div class="pagehead"><p class="eyebrow">Coach</p><h1>Relatórios</h1>
      <p class="sub">O ponto de troca com o seu coach: gere o snapshot para levar ao chat e
        importe o plano de volta. {plan_line}</p></div>
    <div class="grid g2">
      <div class="card"><h2>1 · Exportar snapshot</h2>
        <p class="hint">Estado atual + histórico recente (carga, bem-estar, corridas, cadência,
          decoupling, zonas) para colar no chat do coach.</p>
        <a class="pill solid" style="{btn}" href="/exportar">Gerar snapshot →</a></div>
      <div class="card"><h2>2 · Importar plano</h2>
        <p class="hint">Cole o plano (JSON) que o coach devolveu; o verificador aprova ou
          bloqueia antes de gravar, e você aprova.</p>
        <a class="pill solid" style="{btn}" href="/importar">Importar plano →</a></div>
    </div>
    <div class="card" style="margin-top:16px"><h2>Como funciona</h2>
      <p class="hint">Sincronize os dados → <b>gere o snapshot</b> → cole no Project do Claude
        (seu coach) → ele devolve o plano em JSON → <b>importe e aprove</b> aqui. A partir daí o
        painel rastreia planejado vs. real, e a "Esta semana" (Visão geral) mostra o plano.</p></div>"""


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
    </div>"""


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
NAV = [("/", "Visão geral"), ("/carga", "Carga & recuperação"), ("/treinos", "Treinos"),
       ("/relatorios", "Relatórios"), ("/sincronizacao", "Sincronização")]


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
    return _render("/carga", "Carga & recuperação", page_load)


@app.get("/treinos", response_class=HTMLResponse)
def treinos(request: Request) -> str:
    conn = get_connection()
    try:
        run_migrations(conn)
        t = load_data(conn)
    finally:
        conn.close()
    return shell("/treinos", "Treinos",
                 page_workouts(t, request.query_params.get("w"), request.query_params.get("m")))


@app.get("/recuperacao")
def recuperacao() -> RedirectResponse:
    return RedirectResponse(url="/carga", status_code=307)  # merged into Carga & recuperação


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
        '<p class="cap"><a href="/relatorios" style="color:var(--accent)">← voltar</a></p></div>')
    return shell("/relatorios", "Export", body)


# ------------------------------------------------------------ plan import -----
def _import_form(pasted: str = "") -> str:
    return (
        '<div class="card"><h2>Importar plano do coach</h2>'
        '<p class="hint">Cole o bloco JSON que o coach devolveu. O verificador roda '
        '<b>antes</b> de gravar — plano com violação não é persistido.</p>'
        '<form method="post" action="/importar" data-sync>'
        '<textarea name="plan" spellcheck="false" style="width:100%;height:30vh;margin-top:10px;'
        'font-family:ui-monospace,monospace;font-size:12.5px;background:var(--inset);'
        'color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:12px;'
        f'resize:vertical">{_esc(pasted)}</textarea>'
        '<button class="pill solid" style="border:0;cursor:pointer;margin-top:10px" '
        'data-progress="Verificando…">Verificar e importar</button></form></div>')


def _plan_result_html(result: dict[str, Any]) -> str:
    ok = result["ok"]
    kind, head = ("ok", "Plano verificado e importado — aguarda sua aprovação.") if ok \
        else ("err", "Plano BLOQUEADO pelo verificador — nada foi gravado.")
    parts = [f'<div class="banner b-{kind}" style="margin-top:16px"><span class="bico"></span>'
             f'<div class="btext"><b>{_esc(result["badge"])}</b><span>{head}</span></div></div>']
    for items, color, title in ((result["hard"], "--alert", "Bloqueios — corrija e reenvie ao coach"),
                                (result["soft"], "--warn", "Avisos — passou, mas observe")):
        if items:
            rows = "".join(
                f'<div class="alertrow"><span class="dot" style="background:var({color})"></span>'
                f'<div>{_esc(i["message"])}</div></div>' for i in items)
            parts.append(f'<div class="card" style="margin-top:12px"><h2>{title}</h2>'
                         f'<div class="rlist" style="margin-top:8px">{rows}</div></div>')
    if ok and result.get("plan_id"):
        plan = result["plan"]
        rows = []
        for w in plan["workouts"]:
            d = date.fromisoformat(w["date"])
            lo, hi = w.get("pace_min_per_km"), w.get("pace_max_per_km")
            pace = f"{lo}–{hi}" if lo and hi else (lo or hi)
            rows.append({
                "wd": PT_WD[d.weekday()], "dd": d.day, "cat": _plan_cat(w["type"]),
                "label": PT_TYPE.get(w["type"], w["type"]),
                "metric": _plan_metric(w.get("km"), pace, w.get("hr_target")),
                "note": w.get("note")})
        rat = f'<p class="hint">{_esc(plan["rationale"])}</p>' if plan.get("rationale") else ""
        parts.append(
            f'<div class="card" style="margin-top:12px"><h2>Semana de {_esc(plan["week_start"])} — proposta</h2>'
            f'{rat}{_plan_detail(rows)}'
            f'<form method="post" action="/importar/aprovar?id={result["plan_id"]}" style="margin-top:14px">'
            '<button class="pill solid" style="border:0;cursor:pointer">Aprovar plano</button></form></div>')
    return "".join(parts)


def _page_import(form_html: str, result_html: str, banner: str = "") -> str:
    return (f'{banner}<div class="pagehead"><p class="eyebrow">Import</p><h1>Importar plano</h1>'
            '<p class="sub">Cole o plano do coach; o verificador aprova ou bloqueia antes de gravar.</p>'
            f'</div>{form_html}{result_html}')


@app.get("/importar", response_class=HTMLResponse)
def importar_get(request: Request) -> str:
    banner = (_banner("ok", "Plano aprovado", "Está ativo — o painel vai rastrear planejado vs. real.")
              if request.query_params.get("ok") == "1" else "")
    return shell("/relatorios", "Importar", _page_import(_import_form(), "", banner))


@app.post("/importar", response_class=HTMLResponse)
def importar_post(plan: str = Form("")) -> str:
    from coach import plan_import
    conn = get_connection()
    try:
        run_migrations(conn)
        try:
            result = plan_import.import_plan(conn, plan)
            res_html = _plan_result_html(result)
            pasted = "" if result["ok"] else plan
        except ValueError as exc:
            res_html = ('<div class="banner b-err" style="margin-top:16px"><span class="bico"></span>'
                        f'<div class="btext"><b>Erro ao ler o plano</b><span>{_esc(str(exc))}</span>'
                        '</div></div>')
            pasted = plan
    finally:
        conn.close()
    return shell("/relatorios", "Importar", _page_import(_import_form(pasted), res_html))


@app.post("/importar/aprovar")
def importar_aprovar(request: Request) -> RedirectResponse:
    from coach import plan_import
    pid = request.query_params.get("id")
    if pid and pid.isdigit():
        conn = get_connection()
        try:
            run_migrations(conn)
            plan_import.approve_plan(conn, int(pid))
        finally:
            conn.close()
    return RedirectResponse(url="/importar?ok=1", status_code=303)




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
