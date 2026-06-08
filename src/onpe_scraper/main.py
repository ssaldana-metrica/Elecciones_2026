from __future__ import annotations

import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import OnpeClient
from .exporters import (
    append_candidates_txt,
    load_pending_mesas_txt,
    upsert_agrupaciones_txt,
    upsert_locales_txt,
    upsert_mesas_data_txt,
    upsert_ubicaciones_txt,
    upsert_votos_txt,
    write_pending_mesas_txt,
    write_snapshot_json,
)
from .models import MesaResult


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extraccion base ONPE segunda vuelta 2026 desde API interna"
    )
    parser.add_argument(
        "--modo",
        default="resumen",
        choices=["resumen", "mesas"],
        help="resumen: totales y candidatos (default). mesas: extraccion autonoma por mesa.",
    )

    # --- resumen mode args ---
    parser.add_argument("--id-eleccion", type=int, default=None)
    parser.add_argument(
        "--tipo-filtro",
        default="eleccion",
        help="eleccion, ambito_geografico, ubigeo_nivel_01, ubigeo_nivel_02, ubigeo_nivel_03",
    )
    parser.add_argument("--id-ambito-geografico", type=int, default=None)
    parser.add_argument("--ubigeo", default=None)

    # --- mesas mode args ---
    parser.add_argument(
        "--redescubrir",
        action="store_true",
        help="Ignorar mesas_pendientes.txt y re-descubrir desde assets/data/mesas.json",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=5,
        help="Hilos paralelos para consultas de mesa (maximo: 5)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Mesas por lote antes de escribir a disco",
    )
    parser.add_argument("--timeout", type=int, default=20, help="Segundos por peticion")
    parser.add_argument(
        "--tiempo-max",
        type=int,
        default=0,
        help="Detener scraping despues de N minutos (0 = sin limite). Util para GitHub Actions.",
    )

    parser.add_argument(
        "--no-smart-order",
        action="store_true",
        help="Disable smart ordering (don't prioritize mesas from active districts).",
    )
    parser.add_argument("--intervalo-segundos", type=int, default=0)
    parser.add_argument("--salida", default="output")
    parser.add_argument("--trabajo", default="work", help="Carpeta para archivos intermedios (pendientes, snapshots)")
    parser.add_argument("--verbose", action="store_true")
    return parser


def build_extra_filters(args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if args.tipo_filtro == "ambito_geografico" and args.id_ambito_geografico is not None:
        filters["idAmbitoGeografico"] = args.id_ambito_geografico
    if args.tipo_filtro.startswith("ubigeo_nivel_"):
        if not args.ubigeo:
            raise ValueError("Para filtros ubigeo_nivel_* debes pasar --ubigeo")
        filters["ubigeo"] = args.ubigeo
    return filters


def print_summary(snapshot: dict[str, Any]) -> None:
    process = snapshot.get("activeProcess", {})
    totals = snapshot.get("totales", {})
    candidates = snapshot.get("candidatos", [])

    print(f"Proceso: {process.get('nombre')}")
    print(f"Id eleccion: {snapshot.get('idEleccion')}")
    print(f"Tipo filtro: {snapshot.get('tipoFiltro')}")
    if snapshot.get("filtros"):
        print(f"Filtros: {json.dumps(snapshot['filtros'], ensure_ascii=False)}")
    print(f"Actas contabilizadas: {totals.get('actasContabilizadas')}")
    print(f"Total actas: {totals.get('totalActas')}")
    print(f"Participacion ciudadana: {totals.get('participacionCiudadana')}")
    print("Candidatos:")
    for c in candidates:
        print(
            f"- {c.get('nombreCandidato')} ({c.get('nombreAgrupacionPolitica')}): "
            f"{c.get('porcentajeVotosValidos')}%"
        )


def run_resumen(client: OnpeClient, args: argparse.Namespace, output_dir: Path, work_dir: Path) -> None:
    filters = build_extra_filters(args)
    snapshot = client.get_snapshot(
        election_id=args.id_eleccion,
        tipo_filtro=args.tipo_filtro,
        **filters,
    )

    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = work_dir / f"snapshot_{now}.json"
    txt_path = output_dir / "candidatos_historial.txt"

    write_snapshot_json(snapshot, json_path)
    append_candidates_txt(snapshot, txt_path)

    print_summary(snapshot)
    print(f"Snapshot guardado en: {json_path}")
    print(f"Historial actualizado en: {txt_path}")


def _flush_batch(
    batch_results: list[MesaResult],
    output_dir: Path,
) -> None:
    if not batch_results:
        return
    upsert_mesas_data_txt(batch_results, output_dir / "mesas_data.txt")
    upsert_votos_txt(batch_results, output_dir / "votos.txt")
    upsert_agrupaciones_txt(batch_results, output_dir / "agrupaciones.txt")
    upsert_locales_txt(batch_results, output_dir / "locales.txt")


def run_mesas(client: OnpeClient, args: argparse.Namespace, output_dir: Path, work_dir: Path) -> None:
    # 1. Detect election
    id_eleccion = args.id_eleccion or client.get_active_presidential_election_id()
    print(f"idEleccion: {id_eleccion}")

    # 2. Fetch and write full geographic hierarchy once per run
    print("Descargando jerarquía geográfica...")
    ubicaciones = client.get_ubicaciones(id_eleccion)
    upsert_ubicaciones_txt(ubicaciones, output_dir / "ubicaciones.txt")
    print(f"  {len(ubicaciones)} ubigeos escritos en ubicaciones.txt")

    # 3. Determine which mesas to process using delta approach:
    #    ONPE's /actas gives us exactly which mesas are C/O; we only scrape what's new.
    pending_path = work_dir / "mesas_pendientes.txt"

    contabilized_from_onpe: list[str] = []
    try:
        print("Consultando /actas para obtener mesas contabilizadas...")
        contabilized_from_onpe = client.get_contabilized_mesas(id_eleccion)
        print(f"  {len(contabilized_from_onpe)} mesas C/O desde /actas")
    except Exception as exc:
        print(f"  /actas no disponible ({exc}), usando mesas_pendientes.txt")

    if contabilized_from_onpe:
        # Load what we already have scraped as Contabilizada
        already_done: set[str] = set()
        mesas_data_path = output_dir / "mesas_data.txt"
        if mesas_data_path.exists():
            with mesas_data_path.open("r", encoding="utf-8") as _f:
                for _row in csv.DictReader(_f, delimiter="\t"):
                    if _row.get("descripcion_estado_acta", "").casefold() == "contabilizada":
                        already_done.add(_row["codigo_mesa"])

        # Delta = contabilized by ONPE but not yet in our data
        delta = [m for m in contabilized_from_onpe if m not in already_done]
        # Also retry any previously errored mesas still in pending file
        errored: list[str] = []
        if pending_path.exists():
            pending_set = set(load_pending_mesas_txt(pending_path))
            onpe_set = set(contabilized_from_onpe)
            errored = [m for m in pending_set if m in onpe_set and m not in already_done and m not in set(delta)]
        mesas = delta + errored
        print(
            f"  Delta: {len(delta)} nuevas C/O + {len(errored)} reintentos con error "
            f"(ya completadas: {len(already_done)})"
        )
    elif not args.redescubrir and pending_path.exists():
        mesas = load_pending_mesas_txt(pending_path)
        print(f"Reanudando desde mesas_pendientes.txt: {len(mesas)} mesas")
    else:
        mesas = client.get_all_mesas(election_id=id_eleccion)
        print(f"Mesas descubiertas desde mesas.json: {len(mesas)}")

    max_workers = max(1, min(args.max_workers, 5))
    batch_size = max(1, args.batch_size)
    tiempo_max_s = getattr(args, "tiempo_max", 0) * 60
    start_time = time.time()

    # Smart ordering only needed when falling back to pending list (no /actas delta)
    if not contabilized_from_onpe and not getattr(args, "no_smart_order", False):
        active_ubigeos = client.get_active_ubigeos(id_eleccion)
        known_ubigeo: dict[str, str] = {}
        mesas_data_path = output_dir / "mesas_data.txt"
        if mesas_data_path.exists():
            with mesas_data_path.open("r", encoding="utf-8") as _f:
                for _row in csv.DictReader(_f, delimiter="\t"):
                    known_ubigeo[_row["codigo_mesa"]] = _row["id_ubigeo"]
        active_pending   = [m for m in mesas if known_ubigeo.get(m, "") in active_ubigeos]
        unknown_pending  = [m for m in mesas if m not in known_ubigeo]
        inactive_pending = [m for m in mesas if m in known_ubigeo and known_ubigeo[m] not in active_ubigeos]
        mesas = active_pending + unknown_pending + inactive_pending
        print(
            f"  Orden inteligente — distritos activos: {len(active_ubigeos)} | "
            f"activos: {len(active_pending)} | desconocidos: {len(unknown_pending)} | "
            f"inactivos: {len(inactive_pending)}"
        )

    processed = 0
    errors = 0
    sin_datos = 0  # mesas that returned no data (not yet published by ONPE)
    pending_after: list[str] = []
    batch_results: list[MesaResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit in chunks so we don't queue all 92k futures at once
        for chunk_start in range(0, len(mesas), batch_size):
            chunk = mesas[chunk_start : chunk_start + batch_size]
            futures = {
                executor.submit(client.get_mesa_acta, code, id_eleccion): code
                for code in chunk
            }
            for future in as_completed(futures):
                codigo_mesa = futures[future]
                try:
                    result = future.result()
                    if result is None:
                        # No data yet for this mesa — keep pending, not an error
                        sin_datos += 1
                        pending_after.append(codigo_mesa)
                    else:
                        batch_results.append(result)
                        estado = (result.mesa_data.descripcion_estado_acta if result.mesa_data else "")
                        if estado.casefold() != "contabilizada":
                            pending_after.append(codigo_mesa)
                    processed += 1
                except Exception as exc:
                    errors += 1
                    pending_after.append(codigo_mesa)
                    if args.verbose:
                        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
                        detail = f" (causa: {cause})" if cause else ""
                        print(f"  Error {codigo_mesa}: {exc}{detail}")

            # Write batch to disk and clear buffer
            _flush_batch(batch_results, output_dir)
            batch_results = []

            done = min(chunk_start + batch_size, len(mesas))
            print(
                f"  {done}/{len(mesas)} mesas | "
                f"errores={errors} | sin_datos={sin_datos} | pendientes={len(pending_after)}"
            )

            if tiempo_max_s > 0 and (time.time() - start_time) >= tiempo_max_s:
                # Move unprocessed mesas to the front so next run advances the sweep.
                # Without this, short time windows can keep revisiting the same prefix.
                remaining = mesas[chunk_start + batch_size :]
                pending_after = remaining + pending_after
                print(f"  Tiempo maximo alcanzado ({args.tiempo_max} min). "
                      f"{len(remaining)} mesas sin procesar quedan pendientes.")
                break

    write_pending_mesas_txt(pending_after, pending_path)
    print(
        f"\nCompletado: procesadas={processed} errores={errors} "
        f"sin_datos={sin_datos} pendientes={len(pending_after)}"
    )
    print(f"Salidas en: {output_dir}")


def _run_once(client: OnpeClient, args: argparse.Namespace, output_dir: Path, work_dir: Path) -> None:
    if args.modo == "mesas":
        run_mesas(client, args, output_dir, work_dir)
    else:
        run_resumen(client, args, output_dir, work_dir)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output_dir = Path(args.salida)
    output_dir.mkdir(parents=True, exist_ok=True)

    work_dir = Path(args.trabajo)
    work_dir.mkdir(parents=True, exist_ok=True)

    client = OnpeClient(timeout_seconds=args.timeout if hasattr(args, "timeout") else 20)

    if args.intervalo_segundos <= 0:
        _run_once(client, args, output_dir, work_dir)
        return

    print(f"Iniciando modo continuo cada {args.intervalo_segundos} segundos")
    while True:
        try:
            _run_once(client, args, output_dir, work_dir)
        except Exception as exc:
            print(f"Error durante extraccion: {exc}")
            if args.verbose:
                raise
        time.sleep(args.intervalo_segundos)


if __name__ == "__main__":
    main()
