"""Export a coach snapshot from coach.db — a compact, paste-ready text block for a
chat coach (see PROJECT_SPEC). Writes a file (path configurable via COACH_EXPORT_DIR)
and can print to stdout.

Usage:
    python -m coach.export            # write exports/snapshot.txt and print it
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean

from coach.db import PROJECT_ROOT, get_connection, run_migrations
from coach.load import DEFAULT_REST_HR, banister_trimp

_MONTHS = ["", "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def _dm(d: date) -> str:
    return f"{d.day:02d} {_MONTHS[d.month]}"


def _pace(speed: float | None) -> str:
    if not speed or speed <= 0:
        return "—"
    s = 1000 / speed
    return f"{int(s // 60)}:{int(round(s % 60)):02d}"


def _hms(sec) -> str:
    if not sec:
        return "—"
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _hm(sec) -> str:
    if not sec:
        return "—"
    sec = int(sec)
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}"


def _zone(hr: int | None) -> str:
    if hr is None:
        return "—"
    return "fácil" if hr < 145 else ("moderado" if hr <= 165 else "forte")


def _fc_zones(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT hr_zones_json FROM activity WHERE hr_zones_json IS NOT NULL"
        " ORDER BY start_time_local DESC LIMIT 1").fetchone()
    if not row:
        return "Zonas de FC: (ainda não computadas — rode um sync)"
    zones = [z for z in json.loads(row["hr_zones_json"]) if z.get("low") is not None]
    zones.sort(key=lambda z: z["zone"])
    parts = []
    for i, z in enumerate(zones):
        hi = f"{zones[i + 1]['low'] - 1}" if i + 1 < len(zones) else "máx"
        parts.append(f"Z{z['zone']} {int(z['low'])}–{hi}")
    return "Zonas de FC (config do Garmin, bpm): " + " · ".join(parts)


CROSS_PT = {"strength_training": "força", "cycling": "bike", "indoor_cycling": "bike",
            "lap_swimming": "natação", "open_water_swimming": "natação",
            "walking": "caminhada", "hiking": "caminhada"}


def _zk(hr: int | None) -> str | None:
    if hr is None:
        return None
    return "easy" if hr < 145 else ("mod" if hr <= 165 else "hard")


def _atypical(well: list[dict]) -> str:
    notes = []
    vals = [w["resting_hr"] for w in well if w["resting_hr"] is not None]
    if len(vals) >= 4:
        base = mean(vals[:-1])
        if vals[-1] >= base + 4:
            notes.append(f"FC de repouso elevada ({vals[-1]} vs média {base:.0f})")
        rises = 0
        for i in range(len(vals) - 1, 0, -1):
            if vals[i] > vals[i - 1]:
                rises += 1
            else:
                break
        if rises >= 3:
            notes.append(f"FC de repouso subindo há {rises} dias")
    dep = sum(1 for w in well if w["body_battery_high"] is not None and w["body_battery_high"] < 40)
    if dep:
        notes.append(f"bateria depletada em {dep} dia(s)")
    short = sum(1 for w in well if w["sleep_seconds"] and w["sleep_seconds"] < 6 * 3600)
    if short:
        notes.append(f"sono curto (<6h) em {short} noite(s)")
    return "Atípico: " + ("; ".join(notes) if notes else "nada fora do padrão nos 14 dias.")


def _surface(raw: str | None) -> str:
    try:
        a = json.loads(raw) if raw else {}
    except Exception:
        return "—"
    tk = (a.get("activityType") or {}).get("typeKey", "")
    if "treadmill" in tk or "indoor" in tk:
        return "esteira"
    if "trail" in tk:
        return "trilha"
    if a.get("startLatitude") is None and a.get("startLongitude") is None:
        return "esteira?"
    return "estrada"


def build_snapshot(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row
    out: list[str] = []
    def P(s: str = "") -> None:
        out.append(s)

    P(f"# SNAPSHOT do painel — gerado em {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ---- Carga ----
    load = [dict(r) for r in conn.execute(
        "SELECT date, ctl, atl, tsb, daily_load FROM daily_load ORDER BY date")]
    P("\n## CARGA")
    if load:
        last = load[-1]
        P(f"Hoje ({last['date']}): CTL {last['ctl']:.1f} (fitness/42d) · "
          f"ATL {last['atl']:.1f} (fadiga/7d) · TSB {last['tsb']:+.1f} (forma)")
        P("Série (14 dias) — data: CTL / ATL / TSB · TRIMP do dia:")
        for r in load[-14:]:
            P(f"  {r['date']}: {r['ctl']:.1f} / {r['atl']:.1f} / {r['tsb']:+.1f} · "
              f"TRIMP {r['daily_load']:.0f}")
        base = load[-14]["ctl"] if len(load) >= 14 else load[0]["ctl"]
        d = last["ctl"] - base
        trend = "subindo" if d > 1 else ("caindo" if d < -1 else "estável")
        P(f"Ramp CTL (14d): {trend} ({d:+.1f}/14d).")
        if last["ctl"] < 20:
            P("CAVEAT: CTL baixo (<20) — TSB é ruído, não leia como 'forma'. "
              "Pese os dados brutos de corrida + bem-estar até o CTL passar de ~20.")

    # ---- Fitness ----
    prof = conn.execute("SELECT vo2max, fitness_age FROM user_profile WHERE id=1").fetchone()
    races = [dict(r) for r in conn.execute(
        "SELECT date, time_5k_s, time_10k_s, time_half_s, time_marathon_s"
        " FROM race_prediction ORDER BY date")]
    P("\n## FITNESS")
    if prof:
        P(f"VO2max: {prof['vo2max']:.0f} · idade fitness: {prof['fitness_age']}")
    if races:
        r = races[-1]
        P(f"Previsões (em {r['date']}): 5k {_hms(r['time_5k_s'])} · 10k {_hms(r['time_10k_s'])} · "
          f"21k {_hms(r['time_half_s'])} · 42k {_hms(r['time_marathon_s'])}")
        if len(races) >= 2:
            dd = races[-1]["time_10k_s"] - races[0]["time_10k_s"]
            tr = "melhorando" if dd < -5 else ("piorando" if dd > 5 else "estável")
            P(f"Tendência (desde {races[0]['date']}, {len(races)} snapshots): {tr}")
        else:
            P("Tendência: só 1 dia registrado — cresce com o tempo.")
    P(_fc_zones(conn))
    P("LT1 / LT2: n/d (Garmin não fornece; você define depois) · Zonas de ritmo (E/M/T/I): n/d")

    # ---- Bem-estar (14 dias) ----
    well = list(reversed([dict(r) for r in conn.execute(
        "SELECT * FROM daily_wellness ORDER BY date DESC LIMIT 14")]))
    P("\n## BEM-ESTAR (14 dias)")
    P("dia · FC_rep · sono(dur | prof/leve/REM/acord | FC noturna) · bateria pico/mín · estresse méd/máx")
    for w in well:
        if w["resting_hr"] is None and w["sleep_seconds"] is None and w["body_battery_high"] is None:
            P(f"  {w['date']}: (sem relógio)")
            continue
        if w["sleep_seconds"]:
            sono = (f"{_hm(w['sleep_seconds'])} | {_hm(w['deep_sleep_seconds'])}/"
                    f"{_hm(w['light_sleep_seconds'])}/{_hm(w['rem_sleep_seconds'])}/"
                    f"{_hm(w['awake_sleep_seconds'])} | FC {w['sleep_hr_avg'] or '—'}"
                    f"(mín {w['sleep_hr_min'] or '—'})")
        else:
            sono = "—"
        bb = f"{w['body_battery_high']}/{w['body_battery_low']}" if w["body_battery_high"] is not None else "—"
        st = f"{w['avg_stress']}/{w['max_stress']}" if w["avg_stress"] is not None else "—"
        P(f"  {w['date']}: FC {w['resting_hr'] or '—'} · sono {sono} · bateria {bb} · estresse {st}")
    rhr = [w["resting_hr"] for w in well if w["resting_hr"] is not None]
    slp = [w["sleep_seconds"] for w in well if w["sleep_seconds"]]
    if rhr:
        P(f"Médias: FC repouso {mean(rhr):.0f} (faixa {min(rhr)}–{max(rhr)})"
          + (f" · sono {_hm(int(mean(slp)))}" if slp else ""))
    P(_atypical(well))

    # ---- Atividades (corrida + cross) ----
    acts = [dict(r) for r in conn.execute(
        "SELECT * FROM activity ORDER BY start_time_local")]
    for a in acts:
        a["d"] = date.fromisoformat(a["start_time_local"][:10])
        a["km"] = (a["distance_m"] or 0) / 1000
    runs = [a for a in acts if a["type"] == "running"]
    cross = [a for a in acts if a["type"] != "running"]

    # ---- Semanal (6 semanas) ----
    from collections import defaultdict
    yr = runs[-1]["d"].year if runs else date.today().year
    wk: dict[int, dict] = defaultdict(
        lambda: {"km": 0.0, "runs": 0, "easy": 0.0, "mod": 0.0, "hard": 0.0, "forca": 0, "cross": 0})
    for a in acts:
        b = wk[a["d"].isocalendar()[1]]
        if a["type"] == "running":
            b["km"] += a["km"]; b["runs"] += 1
            z = _zk(a["avg_hr"])
            if z:
                b[z] += a["km"]
        elif "strength" in a["type"]:
            b["forca"] += 1
        else:
            b["cross"] += 1
    P("\n## SEMANAL (últimas 6 semanas com atividade)")
    for n in sorted(wk)[-6:]:
        b = wk[n]
        end = date.fromisocalendar(yr, n, 7)
        P(f"  Semana {n} (fim {_dm(end)}): {b['km']:.0f} km · {b['runs']} corr · "
          f"fácil {b['easy']:.0f}/mod {b['mod']:.0f}/forte {b['hard']:.0f} km · "
          f"força {b['forca']} · cross {b['cross']}")

    # ---- Últimas corridas ----
    obs_max = max((r["max_hr"] or 0) for r in runs) if runs else 190
    rest_by_date = {row["date"]: row["resting_hr"] for row in conn.execute(
        "SELECT date, resting_hr FROM daily_wellness WHERE resting_hr IS NOT NULL")}
    P("\n## CORRIDAS (últimas 8) — data · km · min · ritmo · FC m/máx · cadência · superfície · "
      "elev · TRIMP · decoupling · TE a/an · zona · dor(0-3) · RPE(1-10)")
    for r in list(reversed(runs))[:8]:
        rest = rest_by_date.get(r["d"].isoformat(), DEFAULT_REST_HR)
        trimp = banister_trimp(r["duration_s"], r["avg_hr"], rest, obs_max)
        cad_v = r["avg_cadence"]
        cad = f"{cad_v:.0f}spm" if cad_v else "—"
        low = " [cad.baixa]" if cad_v and cad_v < 130 else ""
        elev = f"+{r['elevation_gain_m']:.0f}m" if r["elevation_gain_m"] else "+0m"
        dec = f"{r['decoupling_pct']:+.1f}%" if r["decoupling_pct"] is not None else "—"
        ate = f"{r['aerobic_training_effect']:.1f}" if r["aerobic_training_effect"] else "—"
        ana = f"{r['anaerobic_training_effect']:.1f}" if r["anaerobic_training_effect"] else "—"
        P(f"  {r['start_time_local'][:10]}: {r['km']:.1f}km · {(r['duration_s'] or 0)/60:.0f}min · "
          f"{_pace(r['avg_speed'])}/km · FC {r['avg_hr'] or '—'}/{r['max_hr'] or '—'} · {cad}{low} · "
          f"{_surface(r['raw_json'])} · {elev} · TRIMP {trimp:.0f} · dec {dec} · "
          f"TE {ate}/{ana} · {_zone(r['avg_hr'])} · dor — · RPE —")
    P("(dor/RPE ainda não capturados — entram quando a UI de feedback existir. "
      "Cadência < 130 spm marca provável caminhada/erro e distorce o decoupling.)")

    # ---- Força / cross ----
    P("\n## FORÇA / CROSS (últimas 8) — data · tipo · min · dor(0-3) · RPE(1-10)")
    if cross:
        for a in list(reversed(cross))[:8]:
            lbl = CROSS_PT.get(a["type"], a["type"])
            P(f"  {a['start_time_local'][:10]}: {lbl} · {(a['duration_s'] or 0)/60:.0f}min · "
              "dor — · RPE —")
    else:
        P("  (nenhuma sessão de força/cross registrada)")

    # ---- Planejado vs realizado ----
    P("\n## PLANEJADO vs REALIZADO (semana corrente)")
    plan = conn.execute("SELECT id FROM weekly_plan WHERE status IN ('active','proposed')"
                        " ORDER BY created_at DESC LIMIT 1").fetchone()
    if plan:
        P("  (há plano ativo — o comparativo detalhado entra quando o coach.import estiver pronto)")
    else:
        P("  Sem plano ativo no painel (o import de plano ainda não está implementado).")

    # ---- Alertas ----
    P("\n## ALERTAS / RECUPERAÇÃO")
    from coach import alerts as alertmod
    fired = alertmod.evaluate(conn, date.today())
    rec = alertmod.recovery_flags(conn)
    if not fired and not rec:
        P("  Nenhum sinal de sobrecarga no momento.")
    for a in fired:
        P(f"  [!] {a['title']}: {a['message']}")
    if rec:
        P(f"  Sinais de recuperação: {', '.join(rec)}")

    # ---- Notas (livre) ----
    P("\n## NOTAS (livre)")
    notas = export_path().parent / "notas.txt"
    txt = notas.read_text(encoding="utf-8").strip() if notas.exists() else ""
    P("  " + txt if txt else "  (vazio — crie exports/notas.txt para incluir observações)")

    return "\n".join(out)


def export_path() -> Path:
    override = os.getenv("COACH_EXPORT_DIR")
    return (Path(override) if override else PROJECT_ROOT / "exports") / "snapshot.txt"


def write_snapshot() -> tuple[Path, str]:
    conn = get_connection()
    try:
        run_migrations(conn)
        text = build_snapshot(conn)
    finally:
        conn.close()
    path = export_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, text


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # avoid cp1252 crash on Windows console
    except Exception:
        pass
    path, text = write_snapshot()
    print(text)
    print(f"\n[snapshot salvo em {path}]")


if __name__ == "__main__":
    main()
