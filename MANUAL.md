# Manual do Chapter Watermarker

Aplicativo web (Flask) para **inserir uma figura ao lado do título de cada
capítulo** de documentos `.docx` e, opcionalmente, aplicar uma **marca d'água
diagonal de texto** em todas as páginas — tudo **gravado no próprio `.docx`**.
Processa **um arquivo, vários ou uma pasta inteira** de uma vez.

---

## 1. Requisitos

- **Python 3.11+**
- Dependências (`requirements.txt`): `Flask`, `python-docx`, `Pillow`,
  `pymupdf` (apenas para aceitar figuras em PDF), `markdown`, `gunicorn`.
- **Não precisa de LibreOffice.** O app gera somente `.docx`.

---

## 2. Como executar

### Modo fácil (instalação local — recomendado)

Não precisa configurar nada. O lançador cria o ambiente, instala as dependências,
define a pasta de saída e abre o navegador:

- **macOS:** duplo-clique em **`run.command`** (ou `bash run.sh` no terminal)
- **Linux:** `bash run.sh`
- **Windows:** duplo-clique em **`run.bat`**

A porta é escolhida automaticamente (contorna o conflito da porta 5000 com o
AirPlay no macOS). Em modo local os resultados em lote são gravados em
`~/Downloads/watermarked`.

Para guardar configurações, crie um arquivo **`local.env`** ao lado do lançador
(não é versionado):

```
LOCAL_SAVE_DIR=/caminho/para/saida
```

### Modo manual

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
LOCAL_SAVE_DIR="$HOME/Downloads" venv/bin/python app.py
```

Páginas:

- **http://127.0.0.1:5000/** — ferramenta principal
- **http://127.0.0.1:5000/manual** — este manual (PDF)
- **http://127.0.0.1:5000/health** — verificação (retorna `ok`)

> A porta real é impressa no terminal (pode ser 5001, 5002… se a 5000 estiver ocupada).

---

## 3. Configuração (variáveis de ambiente)

| Variável | Efeito | Padrão |
|---|---|---|
| `LOCAL_SAVE_DIR` | Ativa o **modo local**: o lote grava os arquivos em `<dir>/watermarked` **e** também oferece o `.zip`. | (não definido → modo download) |
| `PORT` | Porta inicial desejada (auto-incrementa se ocupada). | `5000` |
| `NO_BROWSER` | `1` não abre o navegador ao iniciar. | (abre) |
| `MAX_UPLOAD_MB` | Tamanho máximo por upload. | `1024` |
| `FLASK_DEBUG` | `1`/`true`/`on` ativa o modo debug. | desligado |

---

## 4. Como funciona

O app percorre os parágrafos com estilo **`Heading 1`** (Título 1). Para cada
capítulo, procura uma imagem cujo nome **compartilhe uma palavra** com o título
(correspondência por palavra, não por substring) e a insere ao lado do título.

### Correspondência por palavra (token)

- Ignora números, prefixos e sufixos: basta compartilhar uma palavra
  significativa (≥ 4 letras).
  - `1 Chordata` ↔ `Chordata.png` ✓
  - `Coleoptera I` ↔ `coleoptera.png` ✓
  - `Bryozoa` ↔ `Bryozoa2.jpeg` ✓
- Se **mais de uma** imagem casar, usa a primeira enviada.
- A figura é inserida **apenas na primeira página em que o nome ocorre**;
  ocorrências posteriores são relatadas como já colocadas.

### Layout da figura

- **À direita** do número e nome do capítulo.
- **~30% da largura da página** (ajustável no formulário).
- **Quebra de texto "tight"** (o texto flui ao lado da figura).
- **Opaca (0% de transparência).**
- **Fundo:** PNG/PDF com transparência mantém o fundo removido; caso contrário
  aparece em branco.

### Formatos de figura aceitos

`.png`, `.jpg/.jpeg`, `.tif/.tiff`, `.pdf` (o PDF é rasterizado a partir da
primeira página, preservando transparência de logos vetoriais).

### Status no relatório

| Status | Significado |
|---|---|
| **watermarked** | Figura inserida ao lado do título (mostra o arquivo usado). |
| **skipped — no match** | Nenhuma imagem compartilhou palavra com o capítulo. |
| **duplicate** | Nome já colocado numa página anterior; não repetido. |
| **unused** | Imagens enviadas que nenhum capítulo usou (listadas à parte). |

---

## 5. Marca d'água diagonal (no `.docx`)

No formulário, em **"Diagonal watermark"**:

- **Watermark text** — texto (ex.: `CONFIDENTIAL`, `DRAFT`). Deixe em branco para
  nenhuma marca.
- **Watermark opacity %** — opacidade (25 = 75% transparente).

Quando preenchido, uma marca d'água de texto **diagonal, cinza e semitransparente
é adicionada ao cabeçalho do `.docx`**, aparecendo **atrás do texto em todas as
páginas** (é a marca d'água nativa do Word; continua editável no Word). A saída é
sempre `.docx`.

---

## 6. Lote (vários documentos / pasta)

- Selecione **vários `.docx`** ou **uma pasta inteira** (opção "choose a whole
  folder"). Cada documento é processado separadamente.
- **1 documento** → página de relatório por capítulo + download do `.docx`.
- **Vários documentos** → página-resumo + **`watermarked.zip`** (extrai para uma
  pasta `watermarked/`). Em **modo local**, também grava em
  `<LOCAL_SAVE_DIR>/watermarked/`.

---

## 7. Referência técnica (rotas)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Página principal. |
| GET | `/health` | Retorna `ok`. |
| POST | `/process` | Processa. Campos: `docx` (múltiplos/pasta), `watermarks` (múltiplos), `width_pct`, `diagonal_text`, `diagonal_opacity`. |
| GET | `/download/<token>` | Baixa o resultado (`.docx` ou `.zip`). |
| GET | `/manual` | Este manual (PDF). |

---

## 8. Solução de problemas

| Sintoma | Causa / solução |
|---|---|
| `Port 5000 is in use` (macOS) | AirPlay Receiver. A porta é auto-selecionada; veja a URL impressa no terminal. |
| Figura não aparece num capítulo | Título não está em `Heading 1`, ou nenhuma palavra do título coincide com o nome do arquivo. |
| Marca d'água não aparece | Informe um texto em "Watermark text"; ela fica no cabeçalho/atrás do texto. |
| Upload muito grande | Aumente `MAX_UPLOAD_MB`. |

---

## 9. Estrutura do projeto

```
app.py                 # rotas Flask (inserção de figura + marca d'água + lote)
watermarker.py         # lógica por capítulo (.docx): matching, posição, wrap, marca
build_manual.py        # gera MANUAL.pdf a partir deste MANUAL.md (usa LibreOffice)
run.command / run.sh / run.bat   # lançadores locais
templates/             # index.html, result.html, result_batch.html
requirements.txt
```

### Reconstruir este manual em PDF

```bash
venv/bin/python build_manual.py   # requer LibreOffice só para este passo
```
