# 🇵🇪 Perú 2026 — Dashboard Segunda Vuelta (en vivo)

Dashboard interactivo de la **segunda elección presidencial 2026** que se actualiza solo
desde la API oficial de ONPE, con **proyección al 100% interactiva**.

**Elaborado por: Sergio Saldaña G.**

## ¿Qué muestra?
- Conteo oficial en vivo (votos válidos, % de actas, participación, nulos/blancos).
- Tendencia del margen entre los dos candidatos.
- **Proyección al 100%**: una calculadora de escenarios (no un pronóstico) que estima el
  resultado final según cómo se repartan las actas que faltan, con el umbral honesto que
  necesitaría el segundo lugar para dar vuelta el resultado.

## ¿Cómo funciona?
La API de ONPE exige fingerprint de Chrome y bloquea el acceso directo desde el navegador
(CORS). Por eso el patrón es:

```
update_data.py (fingerprint Chrome)  →  docs/latest.json  →  GitHub Pages
                                                                  ↓
                          el navegador hace fetch a latest.json cada 30s y se redibuja
```

Ver **[DEPLOY.md](DEPLOY.md)** para publicarlo paso a paso.

## Créditos
- **Dashboard, proyección y diseño:** Sergio Saldaña G.
- **Cliente/scraper de la API de ONPE (ingeniería inversa):** basado en
  [oscarzamora/onpe-scraper-2026-2](https://github.com/oscarzamora/onpe-scraper-2026-2).
- **Fuente de datos:** [resultadosegundavuelta.onpe.gob.pe](https://resultadosegundavuelta.onpe.gob.pe) — ONPE.

> Proyecto de transparencia electoral independiente. El resultado oficial lo proclama el JNE.
