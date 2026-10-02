# Deploy no Railway (e a questão de custo)

## Local vs. nuvem

O app agora é **leve**: todo o processamento é `python-docx` + `Pillow` (sem
LibreOffice, sem geração de PDF). Isso o torna barato de hospedar.

- **Rodar localmente (sem Railway) — custo zero.** Use `run.command` (macOS),
  `run.sh` (Linux) ou `run.bat` (Windows). Ideal se você não precisa de URL
  pública.
- **Hospedar no Railway.** Vale se precisa de acesso por URL. Para manter o
  consumo (e o custo) baixos:
  - `gunicorn` com **1 worker** (padrão aqui) e reciclagem de processos.
  - **Modo download** (NÃO defina `LOCAL_SAVE_DIR` — o disco do Railway é efêmero).
  - Ajuste o limite de upload: `MAX_UPLOAD_MB` (ex.: `128`).

> Observação: um app hospedado processa no servidor do Railway; não há como usar
> o seu PC para o cálculo de uma requisição hospedada. Para custo zero, rode
> localmente. Como o app é leve, hospedar também fica barato.

## Deploy pelo GitHub (recomendado)

1. **railway.com → New Project → Deploy from GitHub repo** → escolha o repositório.
2. O Railway detecta `railway.json`/`Procfile` e sobe com:
   `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 300 --max-requests 200`
3. Em **Variables**, defina: `MAX_UPLOAD_MB=128`, `WEB_CONCURRENCY=1`
   (NÃO defina `LOCAL_SAVE_DIR`).
4. **Settings → Networking → Generate Domain** para a URL pública.
5. Healthcheck: `GET /health`. Cada `git push` dispara novo deploy.

## Deploy pela CLI (alternativa)

```bash
brew install railway        # ou: npm i -g @railway/cli
railway login
railway init --name chapter-watermarker
railway variables set MAX_UPLOAD_MB=128 WEB_CONCURRENCY=1
railway up --detach
railway domain
```
