from __future__ import annotations

import csv
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .models import LocalData, MesaResult, UbicacionData


def ensure_output_dir(path: str | Path) -> Path:
    output_dir = Path(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def write_snapshot_json(snapshot: dict[str, Any], output_file: str | Path) -> Path:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return output_path


def append_candidates_txt(
    snapshot: dict[str, Any],
    output_file: str | Path,
) -> Path:
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = snapshot.get("candidatos", [])
    if not isinstance(candidates, list):
        raise ValueError("El campo 'candidatos' no es una lista")

    process = snapshot.get("activeProcess", {})
    totals = snapshot.get("totales", {})

    common = {
        "timestampActualizacion": totals.get("fechaActualizacion"),
        "proceso": process.get("nombre"),
        "idEleccion": snapshot.get("idEleccion"),
        "tipoFiltro": snapshot.get("tipoFiltro"),
        "filtros": json.dumps(snapshot.get("filtros", {}), ensure_ascii=False),
        "actasContabilizadas": totals.get("actasContabilizadas"),
        "totalActas": totals.get("totalActas"),
        "participacionCiudadana": totals.get("participacionCiudadana"),
    }

    fieldnames = [
        "timestampActualizacion",
        "proceso",
        "idEleccion",
        "tipoFiltro",
        "filtros",
        "actasContabilizadas",
        "totalActas",
        "participacionCiudadana",
        "nombreCandidato",
        "nombreAgrupacionPolitica",
        "totalVotosValidos",
        "porcentajeVotosValidos",
        "porcentajeVotosEmitidos",
    ]

    file_exists = output_path.exists()
    with output_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        if not file_exists:
            writer.writeheader()

        for candidate in candidates:
            writer.writerow(
                {
                    **common,
                    "nombreCandidato": candidate.get("nombreCandidato"),
                    "nombreAgrupacionPolitica": candidate.get("nombreAgrupacionPolitica"),
                    "totalVotosValidos": candidate.get("totalVotosValidos"),
                    "porcentajeVotosValidos": candidate.get("porcentajeVotosValidos"),
                    "porcentajeVotosEmitidos": candidate.get("porcentajeVotosEmitidos"),
                }
            )

    return output_path


# ------------------------------------------------------------------ #
# Mesa-level TXT exporters (tab-delimited, upsert by composite key)  #
# ------------------------------------------------------------------ #

_MESAS_DATA_FIELDS = [
    "codigo_mesa",
    "id_eleccion",
    "id_ubigeo",
    "nombre_local_votacion",
    "codigo_local_votacion",
    "id_ambito_geografico",
    "electores_habiles",
    "votos_emitidos",
    "votos_validos",
    "total_asistentes",
    "participacion_ciudadana",
    "codigo_estado_acta",
    "descripcion_estado_acta",
]

_VOTOS_FIELDS = [
    "codigo_mesa",
    "id_eleccion",
    "partido_id",
    "votos",
    "pct_votos_validos",
    "pct_votos_emitidos",
]

_AGRUPACIONES_FIELDS = [
    "partido_id",
    "codigo_op",
    "nombre",
]


def _load_txt(path: Path, key_fields: list[str]) -> OrderedDict[tuple, dict[str, str]]:
    """Load an existing tab-delimited TXT into an OrderedDict keyed by key_fields."""
    store: OrderedDict[tuple, dict[str, str]] = OrderedDict()
    if not path.exists():
        return store
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            key = tuple(row.get(k, "") for k in key_fields)
            store[key] = dict(row)
    return store


def _write_txt(path: Path, fieldnames: list[str], rows: OrderedDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows.values())


def upsert_mesas_data_txt(results: list[MesaResult], output_file: str | Path) -> Path:
    """
    Upsert mesa header rows into a tab-delimited TXT.
    Key: (id_eleccion, codigo_mesa). Only rows with mesa_data are written.
    """
    output_path = Path(output_file)
    store = _load_txt(output_path, ["id_eleccion", "codigo_mesa"])

    for result in results:
        md = result.mesa_data
        if md is None:
            continue
        key = (str(md.id_eleccion), md.codigo_mesa)
        store[key] = {
            "codigo_mesa": md.codigo_mesa,
            "id_eleccion": str(md.id_eleccion),
            "id_ubigeo": str(md.id_ubigeo),
            "nombre_local_votacion": md.nombre_local_votacion,
            "codigo_local_votacion": md.codigo_local_votacion,
            "id_ambito_geografico": str(md.id_ambito_geografico),
            "electores_habiles": str(md.electores_habiles),
            "votos_emitidos": str(md.votos_emitidos),
            "votos_validos": str(md.votos_validos),
            "total_asistentes": str(md.total_asistentes),
            "participacion_ciudadana": str(md.participacion_ciudadana),
            "codigo_estado_acta": md.codigo_estado_acta,
            "descripcion_estado_acta": md.descripcion_estado_acta,
        }

    _write_txt(output_path, _MESAS_DATA_FIELDS, store)
    return output_path


def upsert_votos_txt(results: list[MesaResult], output_file: str | Path) -> Path:
    """
    Upsert vote rows into a tab-delimited TXT.
    Key: (id_eleccion, codigo_mesa, partido_id).
    """
    output_path = Path(output_file)
    store = _load_txt(output_path, ["id_eleccion", "codigo_mesa", "partido_id"])

    for result in results:
        for v in result.votos:
            key = (str(v.id_eleccion), v.codigo_mesa, str(v.partido_id))
            store[key] = {
                "codigo_mesa": v.codigo_mesa,
                "id_eleccion": str(v.id_eleccion),
                "partido_id": str(v.partido_id),
                "votos": str(v.votos),
                "pct_votos_validos": str(v.pct_votos_validos),
                "pct_votos_emitidos": str(v.pct_votos_emitidos),
            }

    _write_txt(output_path, _VOTOS_FIELDS, store)
    return output_path


def upsert_agrupaciones_txt(results: list[MesaResult], output_file: str | Path) -> Path:
    """
    Upsert political organization rows into a tab-delimited TXT.
    Key: partido_id (global catalog, election-independent).
    """
    output_path = Path(output_file)
    store = _load_txt(output_path, ["partido_id"])

    for result in results:
        for ag in result.agrupaciones:
            key = (str(ag.partido_id),)
            store[key] = {
                "partido_id": str(ag.partido_id),
                "codigo_op": ag.codigo_op,
                "nombre": ag.nombre,
            }

    _write_txt(output_path, _AGRUPACIONES_FIELDS, store)
    return output_path


def write_pending_mesas_txt(pending: list[str], output_file: str | Path) -> Path:
    """Overwrite the pending mesas list with the current set of non-counted mesa codes."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for code in pending:
            writer.writerow([code])
    return output_path


def load_pending_mesas_txt(input_file: str | Path) -> list[str]:
    """Read a pending mesas TXT and return normalized 6-digit mesa codes."""
    input_path = Path(input_file)
    codes: list[str] = []
    seen: set[str] = set()
    with input_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            value = row[0].strip()
            if not value or not value.isdigit():
                continue
            code = value.zfill(6)
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes


# ------------------------------------------------------------------ #
# Geographic tables                                                    #
# ------------------------------------------------------------------ #

_UBICACIONES_FIELDS = [
    "ubigeo",
    "ambito",
    "departamento",
    "provincia",
    "distrito",
    "continente",
    "pais",
    "ciudad",
]

_LOCALES_FIELDS = [
    "codigo_local_votacion",
    "nombre_local_votacion",
    "ubigeo",
    "lat",
    "lon",
]


def upsert_ubicaciones_txt(ubicaciones: list[UbicacionData], output_file: str | Path) -> Path:
    """
    Upsert geographic hierarchy rows.
    Key: ubigeo. Preserves any existing lat/lon enrichment on reload.
    """
    output_path = Path(output_file)
    store = _load_txt(output_path, ["ubigeo"])

    for u in ubicaciones:
        key = (u.ubigeo,)
        existing = store.get(key, {})
        store[key] = {
            "ubigeo": u.ubigeo,
            "ambito": u.ambito,
            "departamento": u.departamento,
            "provincia": u.provincia,
            "distrito": u.distrito,
            "continente": u.continente,
            "pais": u.pais,
            "ciudad": u.ciudad,
        }
        # Preserve lat/lon if already enriched (enrich_geo.py may have written them)
        for col in ("lat", "lon"):
            if col not in _UBICACIONES_FIELDS and existing.get(col, ""):
                store[key][col] = existing[col]

    _write_txt(output_path, _UBICACIONES_FIELDS, store)
    return output_path


def upsert_locales_txt(results: list[MesaResult], output_file: str | Path) -> Path:
    """
    Upsert voting-local rows derived from mesa acta data.
    Key: codigo_local_votacion. Preserves lat/lon once enriched by enrich_geo.py.
    """
    output_path = Path(output_file)
    store = _load_txt(output_path, ["codigo_local_votacion"])

    for result in results:
        md = result.mesa_data
        if md is None:
            continue
        key = (md.codigo_local_votacion,)
        existing = store.get(key, {})
        store[key] = {
            "codigo_local_votacion": md.codigo_local_votacion,
            "nombre_local_votacion": md.nombre_local_votacion,
            "ubigeo": md.id_ubigeo,
            "lat": existing.get("lat", ""),
            "lon": existing.get("lon", ""),
        }

    _write_txt(output_path, _LOCALES_FIELDS, store)
    return output_path

