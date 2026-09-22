# Manual do Chapter Watermarker

Aplicativo web (Flask) com duas ferramentas independentes:

1. **Watermark de capítulos** — insere uma marca d'água PNG na primeira página de cada capítulo (`Heading 1`) de um documento `.docx`.
2. **Desenho a carvão (Charcoal)** — transforma fotografias em ilustrações a carvão/grafite, usando um filtro local determinístico **ou** o modelo de imagem do **Gemini** (`gemini-2.5-flash-image`), com processamento individual ou em lote.

---

## 1. Requisitos

- **Python 3.13** (o projeto já traz um ambiente virtual em `venv/`).
- Dependências (`requirements.txt`):
  - `Flask==3.0.3`
  - `python-docx==1.1.2`
  - `Pillow==10.4.0`
  - `numpy==2.1.1`
- Para o modo **IA (Gemini)**: uma chave de API do Google (Gemini). Sem chave, o desenho a carvão funciona apenas no modo **Clássico**.

### Instalação (se precisar recriar o ambiente)

```bash
python3.13 -m venv venv
venv/bin/pip install -r requirements.txt
```

---

## 2. Configuração (variáveis de ambiente)

| Variável | Efeito | Padrão |
|---|---|---|
| `GEMINI_API_KEY` ou `GOOGLE_API_KEY` | Habilita o motor de IA (Gemini). | (nenhuma) |
| `LOCAL_SAVE_DIR` ou `CHARCOAL_OUTPUT_DIR` | Ativa o **modo local**: os PNGs são gravados diretamente nessa pasta do computador. | (não definido → modo download) |
| `PORT` | Porta do servidor. | `5000` |
| `FLASK_DEBUG` | `1`/`true`/`on` ativa o modo debug do Flask. | desligado |

Observações:
- Tamanho máximo de upload: **64 MB** por requisição.
- **macOS:** a porta `5000` costuma estar ocupada pelo *AirPlay Receiver*. Use outra porta (ex.: `5001`).

---

## 3. Como executar

```bash
# Exemplo: porta 5001, com chave do Gemini
PORT=5001 GEMINI_API_KEY="sua-chave" venv/bin/python app.py
```

Acesse no navegador:

- **http://127.0.0.1:5001/** — Watermark de capítulos
- **http://127.0.0.1:5001/charcoal** — Desenho a carvão
- **http://127.0.0.1:5001/health** — verificação de saúde (retorna `ok`)

Modos de saída do carvão:
- **Modo download** (padrão): os resultados são entregues como arquivo para baixar.
- **Modo local** (`LOCAL_SAVE_DIR` definido): os resultados são gravados em disco **e** também disponibilizados para download.

---

## 4. Ferramenta 1 — Watermark de capítulos

### Como funciona
O app percorre os parágrafos com estilo **`Heading 1`** (Título 1). Para cada capítulo, procura uma imagem PNG cujo **nome do arquivo contenha o nome do capítulo** (sem diferenciar maiúsculas/minúsculas). Se houver correspondência, a marca d'água é ancorada, semitransparente, no **canto inferior esquerdo da primeira página do capítulo**.

### Passo a passo
1. Abra a página inicial `/`.
2. **Document (.docx):** selecione o documento Word.
3. **Watermark images (.png):** selecione **uma ou várias** imagens PNG.
4. **Opacity (%):** transparência da marca d'água (1–100; padrão **50**).
5. **Width (% of page):** largura da imagem em relação à largura da página (1–100; padrão **25**). A altura é proporcional e é reduzida se ultrapassar a área útil da página.
6. Clique em **Add watermarks**.
7. Na página de resultado, revise o relatório por capítulo e clique para **baixar** o `.docx` com marca d'água (`<nome>_watermarked.docx`).

### Regra de correspondência (importante)
- O casamento é por **substring**: o nome do capítulo deve aparecer dentro do nome do arquivo PNG.
  - Ex.: capítulo `Chordata` casa com `Chordata.png`, `01-chordata-final.png`, etc.
- O nome **original** do arquivo é usado na comparação (acentos e espaços preservados).

### Status possíveis no relatório
| Status | Significado |
|---|---|
| **watermarked** | Capítulo recebeu a marca d'água (mostra o PNG usado). |
| **skipped — no match** | Nenhum PNG teve o nome do capítulo no nome do arquivo. |
| **ambiguous** | **Mais de um** PNG casou com o capítulo → o capítulo é **pulado** e os candidatos são listados (evita escolher errado). |
| **unused** | PNGs enviados que não foram usados por nenhum capítulo (listados à parte). |

### Dicas
- Garanta que os títulos de capítulo estejam realmente com o estilo **Heading 1** no Word.
- Para evitar `ambiguous`, use nomes de arquivo específicos (ex.: nome exato do capítulo).

---

## 5. Ferramenta 2 — Desenho a carvão (Charcoal)

Fluxo geral: **enviar imagem(ns) → ajustar o "protocolo" (motor/estilo) na primeira imagem → gerar/salvar** (individual ou em lote).

### 5.1 Envio de imagens (`/charcoal`)
- **Photograph(s):** selecione **uma ou muitas** imagens.
- **…or choose a whole folder:** selecione uma **pasta inteira**. Todas as imagens são carregadas; arquivos que não são imagem são ignorados.
- Formatos aceitos: `.png, .jpg, .jpeg, .webp, .gif, .bmp, .tif, .tiff`.

Ao enviar, abre-se o **editor**. Quando há mais de uma imagem, o editor mostra "N images loaded — tuning on <primeira>": você ajusta as configurações na primeira imagem e elas valem para todas.

### 5.2 Motores (Engine)

**Classic filter** (filtro clássico, local e determinístico):
- Rápido, com **pré-visualização ao vivo** enquanto você move os controles.
- **Nunca inventa conteúdo** (é um filtro puro de pixels). Ideal quando fidelidade absoluta é obrigatória.
- Controles:
  - **Smoothing (blur):** suavização das linhas (1–50).
  - **Charcoal depth:** profundidade do carvão em % (0–100).
  - **Contrast:** contraste (0.5–3).
  - **Invert:** giz branco sobre fundo preto.

**AI charcoal (Gemini)** (artístico):
- Usa o modelo `gemini-2.5-flash-image`. Reproduz **o mesmo assunto** da foto (mesma pose, composição, proporções) e apenas troca o meio para carvão — instruído a **não adicionar nem inventar** elementos.
- É mais lento (alguns segundos por imagem) e gera sob demanda (botão **Generate preview**), não ao vivo.
- Aparece somente se houver `GEMINI_API_KEY`/`GOOGLE_API_KEY` no servidor.
- Controles:
  - **Style:** `Detailed (recommended)`, `Bold high-contrast`, `Soft atmospheric`, `Loose sketch`, `White chalk on black`.
  - **Sepia tone:** tons quentes de marrom/sépia em papel creme (padrão **desligado** = cinza neutro).
  - **Extra direction:** texto livre para refinar **apenas o meio/tom** (não altera o assunto). Ex.: "heavier grain".

### 5.3 A direção artística da IA
O resultado da IA segue uma direção fixa de arte a carvão: papel de algodão texturizado branco-sujo com grão visível; pretos profundos e aveludados; alto contraste; hachuras e cross-hatching; realces feitos com "borracha"; **fundo branco limpo** (sem nuvens/gradiente cinza) e apenas leve sombra sob o assunto. O app também envia uma **imagem de referência de estilo** (`references/charcoal_reference.png`) para orientar o traço — sem copiar o assunto dela.

> Sem alucinação: a IA é instruída a reproduzir fielmente o assunto enviado. Ainda assim, por ser um modelo generativo, a fidelidade pixel a pixel não é garantida — para fidelidade 100% garantida, use o motor **Classic**.

### 5.4 Pré-visualização
- **Classic:** atualiza automaticamente ao mover os controles.
- **Gemini:** clique em **Generate preview**. Uma barra de status mostra o progresso e eventuais erros.

### 5.5 Salvar uma imagem (individual)
Na seção **Save**:
- **File name:** nome do arquivo (o `.png` é adicionado automaticamente; o nome original fica gravado nos metadados do PNG).
- **Subdirectory:** subpasta opcional.
  - **Modo download:** se houver subpasta, o resultado vem em um `.zip` que preserva essa pasta; sem subpasta, baixa o `.png` direto.
  - **Modo local:** grava em `LOCAL_SAVE_DIR/<subpasta>/arquivo.png` (nomes duplicados ganham sufixo `_1`, `_2`, …).
- A imagem salva é **exatamente** a que foi pré-visualizada (o resultado do Gemini fica em cache por sessão, evitando uma segunda chamada à API).

### 5.6 Processar em lote → pasta `charcoal/`
Com **2 ou mais** imagens, aparece o botão **"Process all N → /charcoal folder"**:
1. Ajuste o motor/estilo desejado (o "protocolo") na primeira imagem.
2. Clique no botão. Uma sobreposição indica o progresso (no Gemini, ~10 s por imagem).
3. Todas as imagens são renderizadas com o mesmo protocolo e reunidas em uma pasta **`charcoal/`**:
   - **Modo download:** baixa `charcoal.zip` contendo `charcoal/<nome>_charcoal.png`.
   - **Modo local:** grava em `LOCAL_SAVE_DIR/charcoal/` **e** também oferece o `.zip`.
4. A página final lista os arquivos gerados e, se algum falhar (ex.: bloqueio de segurança do Gemini), **apenas aquele é pulado** e listado — os demais são preservados.

Detalhes do lote:
- Nomes de saída: `<nome-original-sem-extensão>_charcoal.png` (colisões recebem `_1`, `_2`, …).
- O nome original é preservado nos metadados de cada PNG.

---

## 6. Referência técnica (rotas)

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Página do watermarker. |
| GET | `/health` | Retorna `ok` (200). |
| POST | `/process` | Aplica watermark. Campos: `docx`, `watermarks` (múltiplos), `opacity`, `width_pct`. |
| GET | `/download/<token>` | Baixa um resultado gerado (docx, png ou zip). |
| GET | `/charcoal` | Página de upload do carvão. |
| POST | `/charcoal/edit` | Recebe `photo` (um/múltiplos/pasta) e abre o editor. |
| GET | `/charcoal/preview/<token>` | Pré-visualização. Parâmetros: `engine` (`classic`/`gemini`), `style`, `sepia`, `detail`, `blur`, `depth`, `contrast`, `invert`. |
| POST | `/charcoal/save` | Salva uma imagem. Campos: `token`, protocolo, `filename`, `subdir`. |
| POST | `/charcoal/batch` | Processa todas em lote para a pasta `charcoal/`. |

Notas de arquitetura:
- Armazenamento em memória, **limitado** (as sessões antigas são descartadas). Nada persiste no servidor além do necessário — pensado para hosts efêmeros (ex.: Railway).
- O motor Gemini fala com a API REST `generateContent` do modelo `gemini-2.5-flash-image` (sem SDK extra); imagens são reduzidas para no máximo 1536 px antes do envio.

---

## 7. Solução de problemas

| Sintoma | Causa provável / solução |
|---|---|
| `Port 5000 is in use` (macOS) | AirPlay Receiver. Rode com `PORT=5001`. |
| Opção "AI charcoal (Gemini)" não aparece | Falta `GEMINI_API_KEY`/`GOOGLE_API_KEY` no servidor. |
| Erro na pré-visualização do Gemini | Mensagem aparece na barra de status (ex.: bloqueio de segurança, falha de rede, chave inválida). Tente outro estilo/imagem ou verifique a chave. |
| "Please upload at least one image." | Nenhum arquivo válido de imagem foi enviado (verifique o formato). |
| Watermark não aplicada em um capítulo | Título não está em `Heading 1`, ou o nome do capítulo não aparece no nome do PNG (status `no_match`), ou vários PNGs casaram (status `ambiguous`). |
| "This editing session expired" | O armazenamento em memória expirou. Reenvie as imagens. |
| Lote muito grande demora/expira | Cada imagem no Gemini leva ~10 s; pastas grandes podem exceder o tempo limite do navegador. Processe em blocos menores. |

---

## 8. Estrutura do projeto

```
app.py                     # rotas Flask (watermark + carvão + lote)
watermarker.py             # lógica de watermark por capítulo (.docx)
charcoal.py                # filtro de carvão local (determinístico) + metadados PNG
gemini_charcoal.py         # motor de IA (Gemini): prompt, estilos, chamada REST
references/
  charcoal_reference.png   # imagem de referência de estilo enviada ao Gemini
templates/                 # páginas HTML (index, charcoal_upload/edit/saved/batch, result)
requirements.txt
```
