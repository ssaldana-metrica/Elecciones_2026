"""Escribe docs/latest.json para el dashboard (totales + candidatos + historial).

Uso:
    python3 update_data.py            # una sola corrida (ideal para GitHub Actions)
    python3 update_data.py 60         # loop: actualiza cada 60s (demo local)
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from src.onpe_scraper.client import OnpeClient

OUT = Path(__file__).parent / "docs" / "latest.json"
HIST_MAX = 720  # ~12 h a 60s


def _num(c, *keys):
    for k in keys:
        if c.get(k) is not None:
            return c[k]
    return None


def build(client: OnpeClient) -> dict:
    proc = client.get_active_process()
    eid = int(proc.get("idEleccionPrincipal") or proc.get("idEleccion") or 0)
    totals = client.get_totals(eid, tipo_filtro="eleccion")
    cands = client.get_candidates(eid, tipo_filtro="eleccion")

    contenders, nulos, blancos = [], 0, 0
    for c in cands:
        party = (c.get("nombreAgrupacionPolitica") or "").upper()
        name = (c.get("nombreCandidato") or "").upper()
        votos = int(_num(c, "totalVotosValidos", "votos") or 0)
        # NULOS / BLANCOS traen codigo (80/81) pero sin nombreCandidato real
        if "NULO" in party or "NULO" in name:
            nulos = votos
        elif "BLANCO" in party or "BLANCO" in name:
            blancos = votos
        elif c.get("nombreCandidato"):  # candidato real
            contenders.append({
                "nombre": c.get("nombreCandidato"),
                "partido": c.get("nombreAgrupacionPolitica"),
                "codigo": c.get("codigoAgrupacionPolitica"),
                "votos": votos,
                "pct_validos": c.get("porcentajeVotosValidos"),
            })

    contenders.sort(key=lambda c: c["votos"], reverse=True)
    cont = int(totals.get("contabilizadas") or 0)
    tot = int(totals.get("totalActas") or 0)

    now_ms = int(time.time() * 1000)
    snap = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_ms": now_ms,
        "onpe_fecha": totals.get("fechaActualizacion"),
        "id_eleccion": eid,
        "proceso": proc.get("nombre"),
        "actas": {"contabilizadas": cont, "total": tot,
                  "pct": totals.get("actasContabilizadas")},
        "participacion": totals.get("participacionCiudadana"),
        "votos_validos": int(totals.get("totalVotosValidos") or 0),
        "votos_emitidos": int(totals.get("totalVotosEmitidos") or 0),
        "nulos": nulos,
        "blancos": blancos,
        "candidatos": contenders,
    }
    return snap


def save(snap: dict) -> None:
    history = []
    if OUT.exists():
        try:
            history = json.loads(OUT.read_text(encoding="utf-8")).get("history", [])
        except Exception:
            history = []

    if len(snap["candidatos"]) >= 2:
        a, b = snap["candidatos"][0], snap["candidatos"][1]
        point = {
            "t": snap["updated_at"][11:],
            "ms": snap["updated_ms"],
            "pct_actas": snap["actas"]["pct"],
            "a_votos": a["votos"], "b_votos": b["votos"],
            "margin": abs(a["votos"] - b["votos"]),
        }
        # evita duplicar si ONPE no avanzo (mismo conteo de actas)
        if not history or history[-1].get("pct_actas") != point["pct_actas"] \
                or history[-1].get("margin") != point["margin"]:
            history.append(point)
    snap["history"] = history[-HIST_MAX:]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT)


def main() -> int:
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    client = OnpeClient()

    def one():
        snap = build(client)
        save(snap)
        a = snap["candidatos"][0] if snap["candidatos"] else {}
        b = snap["candidatos"][1] if len(snap["candidatos"]) > 1 else {}
        print(f"[{snap['updated_at']}] actas {snap['actas']['pct']}% | "
              f"{(a.get('nombre') or '').split()[0]} {a.get('pct_validos')}% vs "
              f"{(b.get('nombre') or '').split()[0]} {b.get('pct_validos')}% | "
              f"-> {OUT.name}", flush=True)

    if interval <= 0:
        one()
        return 0
    print(f"# update loop cada {interval}s -> {OUT}", flush=True)
    while True:
        try:
            one()
        except Exception as exc:
            print(f"[{datetime.now():%H:%M:%S}] error: {exc!r}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
