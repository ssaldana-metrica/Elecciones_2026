"""One-shot live readout from ONPE: votes per candidate + pending actas.

Uses the repo's OnpeClient (Chrome fingerprint already handled).
Run unbuffered:  python3 -u live_resumen.py
"""
from __future__ import annotations

import json
import sys

from src.onpe_scraper.client import OnpeClient


def main() -> int:
    client = OnpeClient()

    # 1) Detect active election
    try:
        proc = client.get_active_process()
    except Exception as exc:
        print(f"[FALLO] No pude leer el proceso activo: {exc!r}")
        return 1

    election_id = int(proc.get("idEleccionPrincipal") or proc.get("idEleccion") or 0)
    print(f"Proceso : {proc.get('nombre')}")
    print(f"idEleccion: {election_id}")
    print("-" * 60)

    # 2) National totals
    try:
        totals = client.get_totals(election_id, tipo_filtro="eleccion")
    except Exception as exc:
        print(f"[FALLO] totales: {exc!r}")
        totals = {}

    fecha = totals.get("fechaActualizacion")
    cont = totals.get("contabilizadas")          # count of actas counted
    pct_actas = totals.get("actasContabilizadas")  # percentage (e.g. 93.647)
    tot = totals.get("totalActas")
    votos_validos = totals.get("totalVotosValidos")
    votos_emitidos = totals.get("totalVotosEmitidos")
    if fecha:
        print(f"Actualizado : {fecha}")
    print(f"Actas       : {cont:,} / {tot:,} contabilizadas ({pct_actas}%)"
          if isinstance(cont, (int, float)) and isinstance(tot, (int, float))
          else f"Actas       : {cont} / {tot} ({pct_actas}%)")
    if isinstance(cont, (int, float)) and isinstance(tot, (int, float)):
        print(f"Pendientes  : {tot - cont:,}")
    print(f"Participacion : {totals.get('participacionCiudadana')}%")
    if isinstance(votos_validos, (int, float)):
        print(f"Votos validos : {votos_validos:,}   (emitidos: {votos_emitidos:,})"
              if isinstance(votos_emitidos, (int, float))
              else f"Votos validos : {votos_validos:,}")
    print("=" * 60)

    # 3) Candidates
    try:
        cands = client.get_candidates(election_id, tipo_filtro="eleccion")
    except Exception as exc:
        print(f"[FALLO] candidatos: {exc!r}")
        cands = []

    # Real contenders only (exclude NULOS / BLANCO rows which have no party code)
    contenders = [c for c in cands if c.get("codigoAgrupacionPolitica")]
    for c in cands:
        v = c.get("totalVotosValidos")
        v_str = f"{v:,}" if isinstance(v, (int, float)) else "—"
        print(
            f"{c.get('nombreCandidato',''):<32} "
            f"{str(c.get('nombreAgrupacionPolitica','')):<22} "
            f"{v_str:>12} votos   {c.get('porcentajeVotosValidos','—')}% val"
        )

    # Margin between the top two contenders
    if len(contenders) >= 2:
        top = sorted(contenders, key=lambda c: c.get("totalVotosValidos") or 0, reverse=True)
        a, b = top[0], top[1]
        va = a.get("totalVotosValidos") or 0
        vb = b.get("totalVotosValidos") or 0
        dpct = (a.get("porcentajeVotosValidos") or 0) - (b.get("porcentajeVotosValidos") or 0)
        print("=" * 60)
        print(f"MARGEN: {a.get('nombreCandidato','').split()[0].title()} adelante por "
              f"{va - vb:,} votos  ({dpct:.3f} puntos)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
