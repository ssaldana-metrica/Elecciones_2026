"""Monitor en vivo ONPE — una linea compacta por ciclo, sin buffering.

Uso:  python3 -u monitor_vivo.py [intervalo_segundos]   (default 60)
Cada linea:  [hora] actas% | Keiko % (votos) | Sanchez % (votos) | margen +N
"""
from __future__ import annotations

import sys
import time
from datetime import datetime

from src.onpe_scraper.client import OnpeClient


def cycle(client: OnpeClient, election_id: int) -> str:
    totals = client.get_totals(election_id, tipo_filtro="eleccion")
    cands = client.get_candidates(election_id, tipo_filtro="eleccion")
    contenders = [c for c in cands if c.get("codigoAgrupacionPolitica")]
    top = sorted(contenders, key=lambda c: c.get("totalVotosValidos") or 0, reverse=True)

    pct_actas = totals.get("actasContabilizadas")
    cont = totals.get("contabilizadas")
    tot = totals.get("totalActas")
    hora = datetime.now().strftime("%H:%M:%S")

    if len(top) < 2:
        return f"[{hora}] datos incompletos: {len(top)} candidatos"

    a, b = top[0], top[1]
    va = a.get("totalVotosValidos") or 0
    vb = b.get("totalVotosValidos") or 0
    na = a.get("nombreCandidato", "?").split()[0].title()
    nb = b.get("nombreCandidato", "?").split()[0].title()
    pend = (tot - cont) if isinstance(tot, (int, float)) and isinstance(cont, (int, float)) else "?"

    return (
        f"[{hora}] actas {pct_actas}% (faltan {pend}) | "
        f"{na} {a.get('porcentajeVotosValidos')}% ({va:,}) | "
        f"{nb} {b.get('porcentajeVotosValidos')}% ({vb:,}) | "
        f"margen {na} +{va - vb:,}"
    )


def main() -> int:
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    client = OnpeClient()

    try:
        election_id = client.get_active_presidential_election_id()
    except Exception as exc:
        print(f"[FATAL] no pude detectar idEleccion: {exc!r}", flush=True)
        return 1

    print(f"# Monitor ONPE en vivo cada {interval}s — idEleccion={election_id} "
          f"(Ctrl-C para parar)", flush=True)
    while True:
        try:
            print(cycle(client, election_id), flush=True)
        except Exception as exc:
            print(f"[{datetime.now():%H:%M:%S}] error: {exc!r}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
