from __future__ import annotations

import random
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from json import JSONDecodeError
from urllib.parse import urlparse
from typing import Any

from curl_cffi import requests as curl_requests

from .models import AgrupacionData, MesaData, MesaResult, UbicacionData, VotoData


BASE_URL = "https://resultadosegundavuelta.onpe.gob.pe/presentacion-backend"
ASSETS_BASE_URL = "https://resultadosegundavuelta.onpe.gob.pe"
_MIN_MESAS_SANITY = 100
_ACTAS_PAGE_SIZE = 100


@dataclass
class OnpeClient:
    base_url: str = BASE_URL
    timeout_seconds: int = 20
    max_retries: int = 5
    _thread_local: threading.local = field(
        default_factory=threading.local, init=False, repr=False, compare=False
    )

    @property
    def _frontend_referer(self) -> str:
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}/main/resumen"

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _request_json_get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._get_curl_session().get(
            url,
            params=params,
            impersonate="chrome124",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except JSONDecodeError as exc:
            snippet = response.text[:180].replace("\n", " ")
            raise RuntimeError(
                f"Respuesta no-JSON desde {url}. status={response.status_code}. "
                f"body~={snippet}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Respuesta JSON inesperada ({type(payload)}): {payload}")
        return payload

    def _get_data(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """curl_cffi GET for aggregate endpoints (Chrome impersonation required by ONPE)."""
        payload = self._request_json_get(f"{self.base_url}{path}", params=params)
        if "data" not in payload:
            raise ValueError(f"Respuesta inesperada sin 'data': {payload}")
        return payload["data"]

    def _build_curl_session(self) -> curl_requests.Session:
        session = curl_requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self._frontend_referer,
                "Accept-Language": "es-PE,es;q=0.9",
            }
        )
        return session

    def _get_curl_session(self) -> curl_requests.Session:
        """Thread-local curl_cffi session (reused across requests in the same thread)."""
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = self._build_curl_session()
            self._thread_local.session = session
        return session

    # ------------------------------------------------------------------ #
    # Summary / aggregate endpoints (use plain requests)                  #
    # ------------------------------------------------------------------ #

    def get_active_process(self) -> dict[str, Any]:
        return self._get_data("/proceso/proceso-electoral-activo")

    def get_active_presidential_election_id(self) -> int:
        process = self.get_active_process()
        election_id = process.get("idEleccionPrincipal")
        if election_id is None:
            raise ValueError(f"No se encontro 'idEleccionPrincipal' en: {process}")
        return int(election_id)

    def get_totals(
        self,
        election_id: int,
        tipo_filtro: str = "eleccion",
        **extra_filters: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "idEleccion": election_id,
            "tipoFiltro": tipo_filtro,
            **extra_filters,
        }
        return self._get_data("/resumen-general/totales", params=params)

    def get_candidates(
        self,
        election_id: int,
        tipo_filtro: str = "eleccion",
        **extra_filters: Any,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "idEleccion": election_id,
            "tipoFiltro": tipo_filtro,
            **extra_filters,
        }
        data = self._get_data(
            "/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
            params=params,
        )
        if not isinstance(data, list):
            raise ValueError(f"Se esperaba lista de candidatos, se obtuvo: {type(data)}")
        return data

    def get_snapshot(
        self,
        election_id: int | None = None,
        tipo_filtro: str = "eleccion",
        **extra_filters: Any,
    ) -> dict[str, Any]:
        if election_id is None:
            election_id = self.get_active_presidential_election_id()

        active_process = self.get_active_process()
        totals = self.get_totals(election_id, tipo_filtro=tipo_filtro, **extra_filters)
        candidates = self.get_candidates(election_id, tipo_filtro=tipo_filtro, **extra_filters)

        return {
            "activeProcess": active_process,
            "idEleccion": election_id,
            "tipoFiltro": tipo_filtro,
            "filtros": extra_filters,
            "totales": totals,
            "candidatos": candidates,
        }

    # ------------------------------------------------------------------ #
    # Mesa-level endpoints (require curl_cffi Chrome impersonation)       #
    # ------------------------------------------------------------------ #

    def get_all_mesas(
        self,
        election_id: int | None = None,
        min_real_mesas: int = _MIN_MESAS_SANITY,
        strict: bool = True,
    ) -> list[str]:
        """
        Return deduplicated, normalized 6-digit mesa codes.

        Strategy:
        1) Try static asset /assets/data/mesas.json.
        2) If asset is missing/invalid, fallback to totalActas and generate [000001..N].

        Placeholder/demo records like 0000/000000 are ignored.
        If fewer than min_real_mesas are found, strict mode raises RuntimeError.
        """
        url = f"{ASSETS_BASE_URL}/assets/data/mesas.json"
        codes: list[str] = []
        try:
            response = self._get_curl_session().get(url, impersonate="chrome124", timeout=self.timeout_seconds)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                seen: set[str] = set()
                for record in data:
                    raw = str(record.get("NUM_MESA", "")).strip()
                    if not raw or not raw.isdigit():
                        continue
                    code = raw.zfill(6)
                    if code == "000000":
                        continue
                    if code not in seen:
                        seen.add(code)
                        codes.append(code)
        except Exception:
            # Some ONPE deployments route unknown assets to SPA HTML (200 + non-JSON).
            codes = []

        if len(codes) >= min_real_mesas:
            return codes

        if election_id is None:
            election_id = self.get_active_presidential_election_id()
        totals = self.get_totals(election_id=election_id, tipo_filtro="eleccion")
        total_actas = int(totals.get("totalActas") or 0)
        if total_actas > 0:
            warnings.warn(
                "No se pudo usar assets/data/mesas.json; usando rango 000001..totalActas para discovery.",
                stacklevel=2,
            )
            codes = [str(i).zfill(6) for i in range(1, total_actas + 1)]

        if len(codes) < min_real_mesas:
            message = (
                f"mesas.json devolvio solo {len(codes)} mesas reales "
                f"(minimo esperado: {min_real_mesas}). "
                "ONPE aun no publica el padron real de mesas para scraping por mesa."
            )
            if strict:
                raise RuntimeError(message)
            warnings.warn(message, stacklevel=2)

        return codes

    def get_ubicaciones(self, id_eleccion: int) -> list[UbicacionData]:
        """
        Fetch the full geographic hierarchy (Peru + exterior) from dep-prov-distritos.

        Peru   (ubigeo prefix 01-25): nombre = DEPARTAMENTO \\ PROVINCIA \\ DISTRITO
        Exterior (prefix 91-95):      nombre = CONTINENTE \\ PAIS \\ CIUDAD
        Nacional (prefix 00):         skipped (aggregate placeholder).
        """
        data = self._get_data("/ubigeos/dep-prov-distritos", params={"idEleccion": id_eleccion})
        if not isinstance(data, list):
            raise ValueError("dep-prov-distritos: se esperaba lista")

        seen: set[str] = set()
        ubicaciones: list[UbicacionData] = []

        for row in data:
            ubigeo = str(row.get("ubigeo") or "").zfill(6)
            if not ubigeo or ubigeo == "000000" or ubigeo in seen:
                continue
            seen.add(ubigeo)

            parts = [p.strip() for p in str(row.get("nombre") or "").split("\\")]
            while len(parts) < 3:
                parts.append("")

            prefix = ubigeo[:2]
            if prefix in ("91", "92", "93", "94", "95"):
                ubicaciones.append(UbicacionData(
                    ubigeo=ubigeo, ambito="exterior",
                    departamento="", provincia="", distrito="",
                    continente=parts[0], pais=parts[1], ciudad=parts[2],
                ))
            else:
                ubicaciones.append(UbicacionData(
                    ubigeo=ubigeo, ambito="peru",
                    departamento=parts[0], provincia=parts[1], distrito=parts[2],
                    continente="", pais="", ciudad="",
                ))

        return ubicaciones

    @staticmethod
    def _parse_acta(acta: dict[str, Any], codigo_mesa: str) -> MesaResult:
        id_eleccion = int(acta.get("idEleccion") or 0)
        mesa_data = MesaData(
            codigo_mesa=codigo_mesa,
            id_eleccion=id_eleccion,
            id_ubigeo=str(int(acta.get("idUbigeo") or 0)).zfill(6),
            nombre_local_votacion=str(acta.get("nombreLocalVotacion") or ""),
            codigo_local_votacion=str(acta.get("codigoLocalVotacion") or ""),
            id_ambito_geografico=int(acta.get("idAmbitoGeografico") or 0),
            electores_habiles=int(acta.get("totalElectoresHabiles") or 0),
            votos_emitidos=int(acta.get("totalVotosEmitidos") or 0),
            votos_validos=int(acta.get("totalVotosValidos") or 0),
            total_asistentes=int(acta.get("totalAsistentes") or 0),
            participacion_ciudadana=float(acta.get("porcentajeParticipacionCiudadana") or 0.0),
            codigo_estado_acta=str(acta.get("codigoEstadoActa") or ""),
            descripcion_estado_acta=str(acta.get("descripcionEstadoActa") or ""),
        )
        agrupaciones: list[AgrupacionData] = []
        votos: list[VotoData] = []
        for item in acta.get("detalle") or []:
            partido_id = int(item.get("adAgrupacionPolitica") or 0)
            if not partido_id:
                continue
            agrupaciones.append(
                AgrupacionData(
                    partido_id=partido_id,
                    codigo_op=str(item.get("adCodigo") or ""),
                    nombre=str(item.get("adDescripcion") or ""),
                )
            )
            votos.append(
                VotoData(
                    codigo_mesa=codigo_mesa,
                    id_eleccion=id_eleccion,
                    partido_id=partido_id,
                    votos=int(item.get("adVotos") or 0),
                    pct_votos_validos=float(item.get("adPorcentajeVotosValidos") or 0.0),
                    pct_votos_emitidos=float(item.get("adPorcentajeVotosEmitidos") or 0.0),
                )
            )
        return MesaResult(
            codigo_mesa=codigo_mesa,
            mesa_data=mesa_data,
            agrupaciones=agrupaciones,
            votos=votos,
        )

    def get_active_ubigeos(self, id_eleccion: int, min_actas: int = 1) -> set[str]:
        """
        Return 6-digit ubigeo codes of all districts with at least min_actas published.

        Uses /resumen-general/mapa-calor at district (nivel_02) granularity, which
        returns all ~2102 districts nationwide with their actasContabilizadas count.
        This lets callers skip mesas in districts where ONPE has not yet published any acta.
        """
        try:
            data = self._get_data(
                "/resumen-general/mapa-calor",
                params={"idEleccion": id_eleccion, "tipoFiltro": "ubigeo_nivel_02"},
            )
        except Exception:
            return set()
        if not isinstance(data, list):
            return set()
        active: set[str] = set()
        for item in data:
            if int(item.get("actasContabilizadas") or 0) >= min_actas:
                ubigeo3 = item.get("ubigeoNivel03")
                if ubigeo3 is not None:
                    active.add(str(int(ubigeo3)).zfill(6))
        return active

    def _get_actas_page(
        self,
        *,
        id_ambito_geografico: int,
        pagina: int,
        tamanio: int = _ACTAS_PAGE_SIZE,
        id_ubigeo: str | None = None,
        codigo_local_votacion: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "idAmbitoGeografico": id_ambito_geografico,
            "pagina": pagina,
            "tamanio": tamanio,
        }
        if id_ubigeo is not None:
            params["idUbigeo"] = id_ubigeo
        if codigo_local_votacion is not None:
            params["codigoLocalVotacion"] = codigo_local_votacion

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self._get_curl_session().get(
                    f"{self.base_url}/actas",
                    params=params,
                    impersonate="chrome124",
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 204:
                    return {
                        "paginaActual": pagina, "totalRegistros": 0,
                        "totalPaginas": 0, "content": [],
                        "contabilizada": 0, "observada": 0, "pendiente": 0,
                    }
                response.raise_for_status()
                if not response.text.strip():
                    return {
                        "paginaActual": pagina, "totalRegistros": 0,
                        "totalPaginas": 0, "content": [],
                        "contabilizada": 0, "observada": 0, "pendiente": 0,
                    }
                payload = response.json()
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise ValueError(f"/actas devolvio payload inesperado: {payload}")
                return data
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.3 * (2**attempt) + random.uniform(0.0, 0.3))
        raise RuntimeError(f"/actas pagina={pagina}: {self.max_retries} intentos fallidos") from last_exc

    def get_contabilized_mesas(self, id_eleccion: int, include_observadas: bool = True) -> list[str]:
        """Placeholder — /actas paging too slow; smart-order fallback is used instead."""
        return []

    def get_mesa_acta(self, codigo_mesa: str, id_eleccion: int) -> MesaResult | None:
        """
        Fetch the acta for a specific mesa and election ID.

        Returns None when the mesa has no data (HTTP 204 or empty body — ONPE returns
        200 with empty body for mesas not yet published, which is NOT an error).
        Retries up to max_retries times with exponential backoff on transient errors.
        Raises RuntimeError after all retries are exhausted.
        """
        codigo_mesa = codigo_mesa.zfill(6)
        url = f"{self.base_url}/actas/buscar/mesa"
        last_exc: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = self._get_curl_session().get(
                    url,
                    params={"codigoMesa": codigo_mesa, "idEleccion": id_eleccion},
                    impersonate="chrome124",
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 204:
                    return None
                response.raise_for_status()
                # ONPE returns HTTP 200 with an empty body for mesas not yet published.
                # Treat this the same as 204 — not an error, just no data yet.
                if not response.text.strip():
                    return None
                payload = response.json()
                break
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    # Exponential backoff with jitter to avoid all workers retrying in lockstep.
                    time.sleep(0.5 * (2**attempt) + random.uniform(0.0, 0.5))
        else:
            raise RuntimeError(
                f"Mesa {codigo_mesa}: {self.max_retries} intentos fallidos"
            ) from last_exc

        data = payload.get("data")
        if not isinstance(data, list):
            return None

        matching = [
            a for a in data if isinstance(a, dict) and a.get("idEleccion") == id_eleccion
        ]
        if not matching:
            return None
        if len(matching) > 1:
            warnings.warn(
                f"Mesa {codigo_mesa}: {len(matching)} actas para idEleccion={id_eleccion}, "
                "usando la primera.",
                stacklevel=2,
            )
        return self._parse_acta(matching[0], codigo_mesa)
