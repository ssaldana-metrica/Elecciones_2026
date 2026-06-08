"""
Enrich output/locales.txt with lat/lon via Nominatim (OpenStreetMap).

Usage:
    python -m src.onpe_scraper.enrich_geo
    python -m src.onpe_scraper.enrich_geo --salida output --force

Nominatim rate limit: 1 request/second (enforced automatically).
Only rows with empty lat/lon are queried unless --force is passed.

Query strategy:
  - Peru   : "<nombre_local>, <distrito>, <provincia>, <departamento>, Peru"
  - Exterior: "<nombre_local>, <ciudad>, <pais>"
  - Fallback: drop the local name and geocode just the address
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json


_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "onpe-scraper-2026/1.0 (https://github.com/oscarzamora/onpe-scraper-2026-2)"
_RATE_LIMIT_S = 1.1  # seconds between requests (Nominatim policy: max 1/s)


def _geocode(query: str) -> tuple[float, float] | None:
    """Query Nominatim, return (lat, lon) or None."""
    params = urlencode({"q": query, "format": "json", "limit": "1"})
    req = Request(
        f"{_NOMINATIM_URL}?{params}",
        headers={"User-Agent": _USER_AGENT},
    )
    with urlopen(req, timeout=10) as resp:
        results = json.loads(resp.read())
    if results:
        return float(results[0]["lat"]), float(results[0]["lon"])
    return None


def _build_query(row: dict[str, str], ubigeo_map: dict[str, dict[str, str]]) -> list[str]:
    """Return a list of query strings to try, from most to least specific."""
    nombre = row["nombre_local_votacion"]
    ubigeo = row["ubigeo"]
    geo = ubigeo_map.get(ubigeo, {})
    ambito = geo.get("ambito", "")

    if ambito == "exterior":
        ciudad = geo.get("ciudad", "")
        pais = geo.get("pais", "")
        return [
            f"{nombre}, {ciudad}, {pais}",
            f"{ciudad}, {pais}",
        ]
    else:
        distrito = geo.get("distrito", "")
        provincia = geo.get("provincia", "")
        dpto = geo.get("departamento", "")
        return [
            f"{nombre}, {distrito}, {provincia}, {dpto}, Peru",
            f"{nombre}, {provincia}, {dpto}, Peru",
            f"{distrito}, {provincia}, {dpto}, Peru",
        ]


def _load_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"No se encontró: {path}")
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    return list(fieldnames), rows


def _write_tsv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(output_dir: Path, force: bool, verbose: bool) -> None:
    locales_path = output_dir / "locales.txt"
    ubicaciones_path = output_dir / "ubicaciones.txt"

    fieldnames, locales = _load_tsv(locales_path)
    _, ubicaciones = _load_tsv(ubicaciones_path)

    # Build ubigeo lookup
    ubigeo_map = {row["ubigeo"]: row for row in ubicaciones}

    # Ensure lat/lon columns exist
    for col in ("lat", "lon"):
        if col not in fieldnames:
            fieldnames.append(col)
    for row in locales:
        row.setdefault("lat", "")
        row.setdefault("lon", "")

    pending = [r for r in locales if force or not r.get("lat")]
    total = len(pending)
    print(f"Locales a geocodificar: {total} (de {len(locales)} total)")

    enriched = 0
    failed = 0

    for i, row in enumerate(pending, 1):
        queries = _build_query(row, ubigeo_map)
        result: tuple[float, float] | None = None

        for q in queries:
            try:
                time.sleep(_RATE_LIMIT_S)
                result = _geocode(q)
                if result:
                    break
            except Exception as exc:
                if verbose:
                    print(f"  [{i}/{total}] Error en query '{q}': {exc}")

        if result:
            row["lat"] = str(result[0])
            row["lon"] = str(result[1])
            enriched += 1
            if verbose:
                print(f"  [{i}/{total}] OK  {row['nombre_local_votacion']} → {result}")
        else:
            failed += 1
            if verbose:
                print(f"  [{i}/{total}] FAIL {row['nombre_local_votacion']}")

        # Checkpoint: save every 50 rows in case of interruption
        if i % 50 == 0:
            _write_tsv(locales_path, fieldnames, locales)
            print(f"  Checkpoint guardado ({i}/{total})")

    _write_tsv(locales_path, fieldnames, locales)
    print(f"\nCompletado: enriched={enriched} failed={failed}")
    print(f"Salida: {locales_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enriquece locales.txt con lat/lon via Nominatim (OpenStreetMap)"
    )
    parser.add_argument("--salida", default="output", help="Carpeta con locales.txt y ubicaciones.txt")
    parser.add_argument("--force", action="store_true", help="Re-geocodificar aunque ya tenga lat/lon")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    run(Path(args.salida), force=args.force, verbose=args.verbose)


if __name__ == "__main__":
    main()
