# Deploy no Railway (e a questão de custo)

## Local vs. nuvem — onde roda o processamento?

Um app **hospedado** no Railway processa no servidor do Railway; não há como
transferir o cálculo de uma requisição hospedada para o seu PC. As escolhas:

- **Rodar localmente (sem Railway) — custo zero.** Use `run.command` (macOS),
  `run.sh` (Linux) ou `run.bat` (Windows). Ideal se você não precisa de URL
  pública. Tudo (inclusive **PDF** e **marca diagonal**) funciona.
- **Hospedar no Railway (enxuto).** Vale se precisa de acesso por URL. Mantenha
  barato assim:
  - **Não instale LibreOffice** no Railway → o export PDF/diagonal fica desativado
    lá (o app mantém o `.docx` e avisa). LibreOffice é o maior consumidor de RAM.
  - `gunicorn` com **1 worker** (padrão aqui) e reciclagem de processos.
  - **Modo download** (NÃO defina `LOCAL_SAVE_DIR` — o disco do Railway é efêmero).
  - Reduza o limite de upload: `MAX_UPLOAD_MB` (ex.: `128`).

Resumo: **watermark em `.docx` → barato no Railway**; **PDF + diagonal → faça no
app local** (sem custo de nuvem).

## Passo a passo (CLI)

```bash
# 1. Instalar e autenticar o CLI (uma vez)
brew install railway            # ou: npm i -g @railway/cli
railway login

# 2. Na pasta do projeto: criar projeto + serviço
railway init --name chapter-watermarker

# 3. Variáveis recomendadas (modo enxuto)
railway variables set MAX_UPLOAD_MB=128 WEB_CONCURRENCY=1

# 4. Deploy
railway up --detach

# 5. Expor uma URL pública
railway domain
```

O Railway detecta o `railway.json`/`Procfile` e sobe com:

```
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 300 --max-requests 200
```

Healthcheck: `GET /health`.

## Habilitar PDF no Railway (opcional, NÃO recomendado por custo)

Exigiria instalar o LibreOffice na imagem (pesado, muita RAM). Se realmente
precisar, adicione um `nixpacks.toml`:

```toml
[phases.setup]
aptPkgs = ["libreoffice"]
```

Prefira manter o PDF no app local.
