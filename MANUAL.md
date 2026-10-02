# Manual do Chapter Watermarker

Aplicativo web (Flask) para **inserir uma figura ao lado do título de cada
capítulo** de documentos `.docx`, com opção de **exportar para PDF** e aplicar
uma **marca d'água diagonal** em todas as páginas. Processa **um arquivo, vários
ou uma pasta inteira** de uma vez.

---

## 1. Requisitos

- **Python 3.11+**
- Dependências (`requirements.txt`): `Flask`, `python-docx`, `Pillow`,
  `pymupdf`, `markdown`.
- **LibreOffice** (opcional) — necessário apenas para **exportar PDF** e para a
  marca d'água diagonal. Sem ele, a inserção de figuras no `.docx` funciona
  normalmente.

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
(correspondência por palavra, não por substring), inserindo-a ao lado do título.

### Correspondência por palavra (token)

- O casamento ignora números, prefixos e sufixos: basta compartilhar uma palavra
  significativa (≥ 4 letras).
  - `1 Chordata` ↔ `Chordata.png` ✓
  - `Coleoptera I` ↔ `coleoptera.png` ✓
  - `Bryozoa` ↔ `Bryozoa2.jpeg` ✓
- Se **mais de uma** imagem casar com o capítulo, uma delas é escolhida (a
  primeira enviada).
- A figura é inserida **apenas na primeira página em que o nome ocorre**;
  ocorrências posteriores são relatadas como já colocadas.

### Layout da figura

- Posicionada **à direita** do número e nome do capítulo.
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

## 5. Exportar PDF e marca d'água diagonal

No formulário:

- **Convert watermarked files to PDF** — converte cada documento para PDF via
  LibreOffice (preserva as figuras inseridas).
- **Diagonal text watermark** — texto (ex.: `CONFIDENTIAL`, `DRAFT`) desenhado
  **na diagonal, em todas as páginas**. Informar um texto **força** a saída PDF.
- **Diagonal opacity %** — opacidade da marca diagonal (25 = 75% transparente).

A conversão é feita **por arquivo**: se um documento falhar/estourar o tempo, os
demais são preservados e o que falhou mantém o `.docx` (relatado na página).

---

## 6. Lote (vários documentos / pasta)

- Selecione **vários `.docx`** ou **uma pasta inteira** (opção "choose a whole
  folder"). Cada documento é processado separadamente.
- **1 documento** → página de relatório por capítulo + download do arquivo.
- **Vários documentos** → página-resumo + **`watermarked.zip`** (extrai para uma
  pasta `watermarked/`). Em **modo local**, também grava em
  `<LOCAL_SAVE_DIR>/watermarked/`.

---

## 7. Referência técnica (rotas)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Página principal. |
| GET | `/health` | Retorna `ok`. |
| POST | `/process` | Processa. Campos: `docx` (múltiplos/pasta), `watermarks` (múltiplos), `width_pct`, `to_pdf`, `diagonal_text`, `diagonal_opacity`. |
| GET | `/download/<token>` | Baixa o resultado (docx, pdf ou zip). |
| GET | `/manual` | Este manual (PDF). |

---

## 8. Solução de problemas

| Sintoma | Causa / solução |
|---|---|
| `Port 5000 is in use` (macOS) | AirPlay Receiver. A porta é auto-selecionada; veja a URL impressa no terminal. |
| Figura não aparece num capítulo | Título não está em `Heading 1`, ou nenhuma palavra do título coincide com o nome do arquivo. |
| "PDF export failed" | LibreOffice ausente ou documento muito grande; o `.docx` é mantido. Instale o LibreOffice. |
| Upload muito grande | Aumente `MAX_UPLOAD_MB`. |

---

## 9. Estrutura do projeto

```
app.py                 # rotas Flask (inserção de figura + PDF + diagonal + lote)
watermarker.py         # lógica por capítulo (.docx): matching, posição, wrap
build_manual.py        # gera MANUAL.pdf a partir deste MANUAL.md
run.command / run.sh / run.bat   # lançadores locais
templates/             # index.html, result.html, result_batch.html
requirements.txt
```

### Reconstruir este manual em PDF

```bash
venv/bin/python build_manual.py
```
