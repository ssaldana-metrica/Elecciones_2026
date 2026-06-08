from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AgrupacionData:
    partido_id: int
    codigo_op: str
    nombre: str


@dataclass(slots=True)
class VotoData:
    codigo_mesa: str
    id_eleccion: int
    partido_id: int
    votos: int
    pct_votos_validos: float
    pct_votos_emitidos: float


@dataclass(slots=True)
class MesaData:
    codigo_mesa: str
    id_eleccion: int
    id_ubigeo: str        # 6-char zero-padded, FK → UbicacionData.ubigeo
    nombre_local_votacion: str
    codigo_local_votacion: str
    id_ambito_geografico: int
    electores_habiles: int
    votos_emitidos: int
    votos_validos: int
    total_asistentes: int
    participacion_ciudadana: float
    codigo_estado_acta: str
    descripcion_estado_acta: str


@dataclass(slots=True)
class MesaResult:
    codigo_mesa: str
    mesa_data: MesaData | None
    agrupaciones: list[AgrupacionData]
    votos: list[VotoData]


@dataclass(slots=True)
class UbicacionData:
    ubigeo: str           # 6-char zero-padded PK
    ambito: str           # "peru" | "exterior"
    departamento: str     # Peru only, else ""
    provincia: str        # Peru only, else ""
    distrito: str         # Peru only, else ""
    continente: str       # Exterior only, else ""
    pais: str             # Exterior only, else ""
    ciudad: str           # Exterior only, else ""


@dataclass(slots=True)
class LocalData:
    codigo_local_votacion: str   # PK
    nombre_local_votacion: str
    ubigeo: str                  # FK → UbicacionData.ubigeo
    lat: float | None            # NULL until enriched via Nominatim
    lon: float | None            # NULL until enriched via Nominatim
