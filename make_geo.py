"""Genera docs/geo.json: split por departamento + macro-regiones + proyeccion neta.

Votos por mesa: de los TSV (mirror ahora; scrape live luego).
% de actas por departamento: en vivo desde ONPE (mapa-calor).
"""
from __future__ import annotations
import csv, json, time
from datetime import datetime
from pathlib import Path
from src.onpe_scraper.client import OnpeClient

REPO = Path(__file__).parent
# Preferir nuestro propio scrape (output/); si no existe, usar el snapshot del mirror.
_OUT = REPO / "output"
TSV = _OUT if (_OUT / "votos.txt").exists() else Path("/Users/ssaldanag")
IS_LIVE = TSV == _OUT
OUT = REPO / "docs" / "geo.json"
KEIKO, SANCHEZ = "8", "10"

MACRO = {
    "Lima Metropolitana": {"LIMA", "CALLAO"},
    "Norte": {"TUMBES", "PIURA", "LAMBAYEQUE", "LA LIBERTAD", "CAJAMARCA", "AMAZONAS"},
    "Centro": {"ÁNCASH", "HUÁNUCO", "PASCO", "JUNÍN", "HUANCAVELICA", "ICA"},
    "Sur": {"AREQUIPA", "MOQUEGUA", "TACNA", "CUSCO", "PUNO", "APURÍMAC", "AYACUCHO"},
    "Oriente": {"LORETO", "SAN MARTÍN", "UCAYALI", "MADRE DE DIOS"},
}
def macro_of(dep, amb):
    if amb == "exterior":
        return "Exterior"
    for m, deps in MACRO.items():
        if dep in deps:
            return m
    return "Otros"


def main():
    c = OnpeClient()
    ubic = c.get_ubicaciones(10)
    # prefijo de 2 digitos -> (departamento, ambito)
    pref2dep, pref2amb = {}, {}
    for u in ubic:
        p = u.ubigeo[:2]
        # Peru: por departamento. Exterior: TODO agrupado como "Exterior".
        pref2dep.setdefault(p, u.departamento if u.ambito == "peru" else "Exterior")
        pref2amb.setdefault(p, u.ambito)

    # --- votos por prefijo (mesa-level) ---
    mesa_pref, counted_actas, counted_valid = {}, {}, {}
    with open(TSV / "mesas_data.txt", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            p = str(r["id_ubigeo"]).zfill(6)[:2]
            mesa_pref[r["codigo_mesa"]] = p
            counted_actas[p] = counted_actas.get(p, 0) + 1
            counted_valid[p] = counted_valid.get(p, 0) + int(r["votos_validos"] or 0)
    votes = {}
    with open(TSV / "votos.txt", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["partido_id"] not in (KEIKO, SANCHEZ):
                continue
            p = mesa_pref.get(r["codigo_mesa"])
            if not p:
                continue
            d = votes.setdefault(p, {KEIKO: 0, SANCHEZ: 0})
            d[r["partido_id"]] += int(r["votos"] or 0)

    # --- % de actas EN VIVO por prefijo (mapa-calor nivel_01, granularidad provincia) ---
    live_cont, live_total = {}, {}
    try:
        mc = c._get_data("/resumen-general/mapa-calor",
                         params={"idEleccion": 10, "tipoFiltro": "ubigeo_nivel_01"})
        for row in mc:
            n1 = row.get("ubigeoNivel01")
            if n1 is None:
                continue
            p = str(int(n1)).zfill(6)[:2]
            cont = row.get("actasContabilizadas") or 0
            pct = row.get("porcentajeActasContabilizadas") or 0
            live_cont[p] = live_cont.get(p, 0) + cont
            if pct > 0:
                live_total[p] = live_total.get(p, 0) + cont / (pct / 100.0)
    except Exception as e:
        print("mapa-calor live FAIL:", repr(e)[:120])

    # Sumar todo por NOMBRE de departamento (asi el exterior se une en uno solo)
    by = {}
    for p, v in votes.items():
        dep = pref2dep.get(p, f"[{p}]")
        amb = pref2amb.get(p, "peru")
        a = by.setdefault(dep, {"amb": amb, "k": 0, "s": 0, "cont": 0, "total": 0,
                                "cvalid": 0, "cactas": 0})
        a["k"] += v[KEIKO]; a["s"] += v[SANCHEZ]
        a["cont"] += live_cont.get(p, counted_actas.get(p, 0))
        a["total"] += live_total.get(p, live_cont.get(p, counted_actas.get(p, 0)))
        a["cvalid"] += counted_valid.get(p, 0)
        a["cactas"] += counted_actas.get(p, 0)

    deps = []
    for dep, a in by.items():
        tot = a["k"] + a["s"]
        if tot == 0:
            continue
        kp, sp = 100 * a["k"] / tot, 100 * a["s"] / tot
        cont, total = round(a["cont"]), round(a["total"])
        pend_actas = max(0, total - cont)
        vpa = a["cvalid"] / max(1, a["cactas"])
        pend_votos = round(pend_actas * vpa)
        net = round(pend_votos * (kp - sp) / 100)   # +Keiko / -Sanchez
        deps.append({"dep": dep, "amb": a["amb"], "macro": macro_of(dep, a["amb"]),
                     "k": a["k"], "s": a["s"], "kp": round(kp, 1), "sp": round(sp, 1),
                     "actas_cont": cont, "actas_tot": total,
                     "actas_pct": round(100 * cont / total, 1) if total else None,
                     "pend_actas": pend_actas, "pend_votos_est": pend_votos, "net": net})
    deps.sort(key=lambda d: d["kp"], reverse=True)

    # macro agg
    macro = {}
    for d in deps:
        m = macro.setdefault(d["macro"], {"k": 0, "s": 0})
        m["k"] += d["k"]; m["s"] += d["s"]
    for m, v in macro.items():
        t = v["k"] + v["s"]
        v["kp"] = round(100 * v["k"] / t, 1) if t else 0
        v["sp"] = round(100 * v["s"] / t, 1) if t else 0

    tot_k = sum(d["k"] for d in deps)
    tot_s = sum(d["s"] for d in deps)
    geo = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_ms": int(time.time() * 1000),
        "source": ("scrape propio · ~93% actas (base + refresh pendientes)" if IS_LIVE
                   else "votos: snapshot mirror · % actas: ONPE en vivo"),
        "national": {"k": tot_k, "s": tot_s,
                     "kp": round(100 * tot_k / (tot_k + tot_s), 2),
                     "sp": round(100 * tot_s / (tot_k + tot_s), 2)},
        "net_total": sum(d["net"] for d in deps),
        "departments": deps,
        "macro": macro,
    }
    OUT.write_text(json.dumps(geo, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"-> {OUT}  ({len(deps)} deptos)")
    print(f"{'Depto':16}{'K%':>6}{'S%':>6}{'actas%':>8}{'pend.votos':>11}{'neto':>10}")
    for d in deps:
        print(f"{d['dep'][:15]:16}{d['kp']:>6}{d['sp']:>6}{str(d['actas_pct']):>8}"
              f"{d['pend_votos_est']:>11,}{d['net']:>10,}")
    print(f"\nMacro: " + " | ".join(f"{m}:{v['kp']}/{v['sp']}" for m, v in macro.items()))
    print(f"Neto total proyectado (suma): {geo['net_total']:+,}  (+ = Keiko)")


if __name__ == "__main__":
    main()
