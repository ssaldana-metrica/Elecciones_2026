# Dashboard ONPE 2026 — cómo publicarlo

Dashboard interactivo de la segunda vuelta presidencial 2026 que se actualiza solo
(la gente abre un link y ve los números moverse, sin tocar nada) + proyección al 100%
interactiva.

## Qué es cada archivo
| Archivo | Para qué |
|---|---|
| `docs/index.html` | El dashboard. Hace `fetch('latest.json')` cada 30s y se redibuja solo. |
| `docs/latest.json` | Los datos (lo genera el updater). |
| `update_data.py` | Llama a la API de ONPE (con fingerprint de Chrome) y escribe `docs/latest.json`. |
| `.github/workflows/dashboard.yml` | Corre el updater en la nube cada ~5 min y publica. |

---

## Verlo YA en tu máquina (refresco real cada 60s)

```bash
# 1) updater en loop (cada 60s) — deja esta terminal abierta
python3 -u update_data.py 60

# 2) en OTRA terminal, sirve el dashboard
python3 -m http.server 8000 --directory docs
# abre http://localhost:8000
```

---

## Publicarlo como link público (GitHub Pages)

> Necesitas TU propio repo en GitHub (no uses el de otra persona).

```bash
# en la carpeta del proyecto
git remote remove origin                       # quita el remote ajeno
git config user.name  "Tu Nombre"
git config user.email "tu@email.com"
# crea un repo vacío en github.com (ej: onpe-2026) y luego:
git remote add origin https://github.com/TU_USUARIO/onpe-2026.git
git add docs update_data.py .github/workflows/dashboard.yml DEPLOY.md
git commit -m "dashboard ONPE 2026 en vivo"
git branch -M main
git push -u origin main
```

Luego en GitHub:
1. **Settings → Pages → Build and deployment → Source: _Deploy from a branch_ → Branch: `main` / `/docs`** → Save.
2. **Settings → Actions → General →** asegúrate de permitir _Read and write permissions_ (para que el bot pueda hacer commit de `latest.json`).
3. En **Actions**, corre el workflow "ONPE dashboard" una vez (botón _Run workflow_) para generar el primer dato.

Tu link queda en: **`https://TU_USUARIO.github.io/onpe-2026/`**

### ⚠️ Limitaciones honestas del modo nube
- El cron de GitHub Actions corre **como mínimo cada ~5 min** y a veces se atrasa: el dato
  en la nube NO es de 60s, es de ~5–10 min.
- GitHub Actions corre desde IPs de datacenter fuera de Perú. **No está garantizado** que
  ONPE responda desde ahí (puede filtrar por geografía/IP además del fingerprint). Si el
  workflow falla en el paso "Scrape ONPE", usa el fallback de abajo.

---

## Fallback que SIEMPRE funciona: updater local + Pages

Si la nube no puede con ONPE, corre el updater en tu Mac y que él haga el push.
Refresca cada 60s de verdad y Pages sirve el HTML.

```bash
# cron cada minuto (crontab -e):
* * * * * cd /ruta/al/proyecto && /usr/bin/python3 update_data.py && \
  git commit -am "data [skip ci]" >/dev/null 2>&1 && git push >/dev/null 2>&1
```

(En este modo puedes borrar `.github/workflows/dashboard.yml` para no duplicar.)
