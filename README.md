# RelMeg — Plataforma de Monitoramento Legislativo e Inteligência Parlamentar

> **Criado por:** Victor Souza de Aguiar
> **Desenvolvido para:** Family Talks (OSCIP dedicada ao fortalecimento da família no Brasil)

---

## O que é

O **RelMeg** é uma plataforma completa de monitoramento legislativo e inteligência de
stakeholders. Ele **centraliza e consolida dados de múltiplas APIs públicas do governo
federal e estadual** — Câmara dos Deputados, Senado Federal, TSE (DivulgaCandContas),
DOU (Diário Oficial da União), CLDF, ALESP e ALMG — e os transforma em **relatórios
acionáveis** para advocacy e acompanhamento de pautas familiares no Congresso Nacional.

Atua como um **hub inteligente**: conecta fontes governamentais dispersas, aplica uma
matriz de inteligência própria (Family Talks) para filtrar só o que interessa, e entrega
documentos prontos para consumo (Word, PDF, Excel e JSON).

---

## Para que serve

- **Monitorar o Congresso Nacional** em tempo real sobre pautas familiares (criança e
  adolescente, licenças parentais, proteção à infância, idosos, educação, violência
  doméstica e segurança digital);
- **Identificar oportunidades de advocacy**: o operador sabe, em segundos, quais
  proposições novas foram apresentadas no período e em qual tema elas se encaixam;
- **Alimentar o diálogo bipartidário** com dados confiáveis e formatados — sem depender
  de coleta manual;
- **Produzir entregas recorrentes** (clipping semanal, relatórios, fichas, planilhas de
  candidatos) com identidade visual padronizada da casa;
- **Dar suporte a eleições**: extração, enriquecimento e consolidação de candidatos do
  TSE com perfil, patrimônio, propostas e redes sociais.

---

## O que faz

### Fontes de dados integradas

| Fonte | Dados coletados |
|-------|----------------|
| **Câmara dos Deputados** | Deputados, proposições, eventos, autores, frentes parlamentares, busca por palavras-chave, varredura por janela de datas e status legislativo |
| **Senado Federal** | Matérias legislativas, comissões, relatórios e pareceres |
| **TSE (DivulgaCandContas)** | Candidatos (2018–2026), enriquecimento por candidato e **detalhe rico**: perfil, bens individuais + agregados, propostas e redes sociais |
| **CLDF** | Proposições do PLE (DF) via API pública; **histórico de tramitação** via raspagem (a API só expõe a etapa atual) |
| **DOU (Diário Oficial da União)** | Publicações por palavra-chave, data, ano e seção |
| **ALGO** | Esqueleto registrado (fonte reconhecida; responde 501 na V1) |
| **ALMG / ALESP** | Registradas; mapeamento futuro (CLDF é o piloto ALE) |

### Resiliência (Fallback Scrapling)

Quando a API oficial de uma fonte falha (403/503/timeout) ou devolve vazio, o motor tenta
**raspar a página pública** com o Scrapling (`USA_SCRAPLING=true`) — Câmara (ficha de
tramitação), Senado (linha do tempo), CLDF (histórico completo renderizado). Se mesmo a
raspagem falhar, o conector devolve o que tem, sem jamais levantar. Toda raspagem ocorre
dentro de uma requisição on-demand do operador (AGENTS.md).

### Filtro Inteligente Family Talks

Filtro determinístico que cruza cada ementa com a **matriz de temas prioritários**:

- **Temas prioritários:** licença parental, proteção infantil, violência familiar,
  primeira infância, segurança digital, cuidados com idosos, educação, assistência social;
- **Descartados automaticamente:** divórcio, alienação parental, direito penal puro,
  reforma tributária;
- **Proteção da infância prevalece:** pauta penal que protege crianças/adolescentes
  (ex.: endurecer o combate à violência sexual infantil gerada por IA) **não** é descartada.

### Produtos gerados

1. **Clipping Semanal (.docx)** — documento Word clonado do template **"MODELO BASE.docx"**
   (identidade da casa: fonte Montserrat, texto `333333`, links em negrito vermelho
   `fe0000` sublinhado, caixa de período dinâmica "11/09 - 14/09"), com proposições
   filtradas pela matriz Family Talks, prontas para envio via WhatsApp;
2. **Relatório Executivo (.pdf)** — relatório corporativo com KPIs, resumos temáticos e
   tabelas (ReportLab);
3. **Planilha TSE (.xlsx)** — exportação estruturada de candidatos seguindo o
   "MODELO BASE" (25 colunas) e **modelo MULTIABA** (multi-abas), compatível com BI
   (Looker Studio, Google Sheets);
4. **Ficha Legislativa / Ficha de Parlamentar (.docx)** — dossiê individual de proposição
   ou parlamentar, gerado do repositório local;
5. **Dossiê Rico TSE (JSON)** — detalhes máximos por candidato (perfil, bens individuais
   + agregados, propostas estruturadas, redes sociais com plataforma), persistidos e
   legíveis sem nova consulta à API;
6. **Planilha de Coleta de Perfil (.xlsx)** — gabarito do "Modelo base de coleta -
   Parlamentares.xlsx" pre-preenchido com Casa/Nome/Partido/UF para trabalho de campo,
   gravado em `Perfil/`.

---

## Como usar

### Requisitos

- Python 3.10+ (o backend roda em `backend/` com seu próprio `.venv`);
- Windows (padrão de desenvolvimento) — os scripts de ativação do venv abaixo são para
  PowerShell; equivalentes Unix no comentário.

### Instalação

```bash
git clone https://github.com/victoripolunb-dev/RelMEg.git
cd RelMEg

# Ambiente virtual
python -m venv backend\.venv
backend\.venv\Scripts\activate        # Windows
# source backend/.venv/bin/activate   # Linux/Mac

# Dependências
pip install -r backend/requirements.txt
# Fallback de raspagem (opcional, somente se for ativar o Scrapling):
pip install -r backend/requirements-extras.txt

# Variáveis de ambiente
copy .env.example .env                # Windows
# cp .env.example .env                # Linux/Mac
```

### Executar

```bash
cd backend
..\backend\.venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 8000
# Documentação interativa: http://localhost:8000/docs
```

### Fluxos principais do operador

| Objetivo | Ação |
|----------|------|
| **Gerar clipping semanal de proposições novas** | `POST /api/exportar/clipping-semanal` com `data_inicio`/`data_fim` (janela do período); o sistema varre Câmara e Senado por sigla e data, filtra pela matriz e monta o `.docx` em `~/Desktop/RelMeg - Entregas/Novas proposições/` |
| **Buscar proposições por palavra-chave** | `GET /hub/busca/proposicoes?fonte=camara&keywords=...` |
| **Extrair candidatos do TSE** | `GET /tse/exportar/{ano}/{uf}/{cargo}` (retorna 202 + `task_id`; acompanhe em `GET /tse/execucoes/{task_id}`) |
| **Montar ficha legislativa** | `POST /hub/exportar/ficha` passando a proposição já coletada |
| **Pesquisar no DOU** | `GET /dou/pesquisa?q=...` |

---

## Principais rotas

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/hub/fontes` | GET | Status das fontes do motor (sem rede). |
| `/hub/busca/proposicoes` | GET | Busca por palavras-chave na fonte (`camara` e `dou` → 200; outras → 501). Sob demanda. |
| `/hub/proposicoes/listar` | GET | Proposições salvas no repositório local. |
| `/hub/proposicoes/{fonte}/{id}` | GET/POST | Ler salva / disparar coleta (gatilho on-demand). Inclui `autorias`. |
| `/hub/parlamentares/listar` | GET | Parlamentares salvos. |
| `/hub/parlamentares/{fonte}/{id}` | GET/POST | Ler salva (com `proposicoes_autoradas`) / disparar coleta. |
| `/hub/exportar/ficha` | POST | Ficha Legislativa (.docx) de proposição já coletada. |
| `/hub/exportar/ficha-parlamentar` | POST | Ficha de Parlamentar (.docx). |
| `/hub/exportar/planilha-coleta` | POST | Planilha de Coleta de Perfil (.xlsx do MODELO BASE) a partir dos parlamentares salvos. |
| `/api/exportar/clipping-semanal` | POST | Clipping de Novas Proposições (.docx) com janela de datas e filtro Family Talks. |
| `/tse/exportar/{ano}/{uf}/{cargo}` | GET | **Trigger de extração** (202 + task_id). `campos=` restringe blocos; vazio = máximo. |
| `/tse/execucoes/{task_id}` | GET | Status e log de etapas da extração. |
| `/tse/detalhe/{cache_key}/{id_candidato}` | GET | Dossiê rico do candidato (cache local, sem rede). |
| `/tse/candidatos` | GET | Lista candidatos com filtros avançados. |
| `/tse/candidato/{ano}/{uf}/{id}` | GET | Detalhe ao vivo do TSE (rede). |
| `/deputados/`, `/proposicoes/`, `/eventos/`, `/frentes/` | GET | Câmara (rotas clássicas). |
| `/senado/materias/`, `/senado/comissoes/` | GET | Senado. |
| `/dou/pesquisa` | GET | Pesquisa no Diário Oficial. |

---

## Que resultado o sistema entrega

- **Clipping semanal** identifica todas as proposições novas do período (varredura por
  janela de datas e por sigla, sem depender de palavras-chave), as principais **20 itens**
  relevantes para a Family Talks e salva o Word pronto no destino de entregas;
- **Relatórios PDF e fichas .docx** com identidade visual padronizada (clonagem fiel do
  modelo, nunca sobrescrita);
- **Extração TSE completa** com cache local SQLite, enriquecimento por candidato e dossiês
  ricos — tudo disponível **offline** após a coleta;
- **Log de auditoria** em `backend/data/relmeg_cache.db`, com retenção configurável, para
  rastrear cada operação.

---

## Princípio arquitetural: execução estritamente sob demanda

**Nenhuma** chamada a APIs governamentais ocorre sem ação explícita do operador. Proibido:
cron, polling em segundo plano, startup/lifespan que disparem varreduras ou
auto-carregamento de abas no frontend (AGENTS.md).

### Única exceção autorizada: `BackgroundTasks` em extração pesada sob demanda

Aprovada em 07/09/2026 pelo operador, com requisitos rígidos:

- **Disparo exclusivo por requisição HTTP explícita**: a `BackgroundTask` é registrada
  APENAS dentro da rota `/tse/exportar/{ano}/{uf}/{codigo_cargo}` (o próprio trigger
  on-demand) e retorna 202 + `task_id`, com status em `GET /tse/execucoes/{task_id}` e
  log de etapas persistido em `backend/database.py`;
- A tarefa baixa o payload inicial no próprio handler e a varredura de enriquecimento
  sempre passa pelo cache SQLite local (nenhuma reutilização de cache após falha da
  primeira chamada);
- **Nunca** agendar, cron, dispatcher automático ou polling de fila — o worker é inerte
  sem a requisição do operador;
- Rate limits (slowapi) mantidos na rota de disparo, inclusive durante o processamento;
- Recusa (400/409) de segunda tarefa concorrente para os mesmos filtros enquanto uma
  execução do mesmo escopo estiver "Executando".

---

## Por que é tão importante

1. **Economia de trabalho manual**: dezenas de horas de leitura de proposições são
   substituídas por um clique — o clipping que antes levava um dia sai em segundos.
2. **Ampla cobertura sem perder nada**: a varredura por janela de datas + siglas captura
   o período inteiro, e o filtro automatizado descarta o ruído preservando o que interessa.
3. **Advocacy baseado em dado**: a Family Talks chega ao Congresso sabendo exatamente o
   que foi apresentado, quem assinou e em que tema a pauta se enquadra.
4. **Deliverables com cara de produto**: Word/PDF/Excel com identidade visual da casa,
   prontos para WhatsApp, e-mails e relatórios executivos.
5. **Segurança e bom uso de API**: sob demanda, com rate-limit, cache local e retry com
   backoff — protegendo o acesso às APIs públicas (evita bloqueios institucionais).
6. **Confiança eleitoral**: o TSE é rastreado com dossiês ricos por candidato, úteis para
   análise de biografia, patrimônio e propostas.

---

## Entregas ao cliente (convenção obrigatória)

- **Pasta única de entregas**: `~/Desktop/RelMeg - Entregas/` (config `settings.dir_entregas`),
  em subpasta temática (`Relatórios/`, `Novas proposições/`, `TSE/`, `DOU/`, `Dashboards/`,
  `Perfil/`). Nunca deixar entregas na raiz do repositório;
- **Formato padrão Word (.docx)** para relatórios e clippings — python-docx com a
  identidade da casa (Montserrat, `333333`, links `fe0000` em negrito, como em
  `backend/servicos/exportador_local.py`). Arquivos de apoio (JSON/Excel) acompanham o
  .docx na mesma subpasta;
- **Validação**: o template (ex.: `MODELO BASE.docx`) nunca é sobrescrito — o exportador
  sempre clona a partir dele.

---

## Segurança

- **API Key**: todas as rotas (exceto `/`, `/docs`, `/redoc`, `/openapi.json`) exigem
  `X-API-Key` quando `RELMEG_API_KEY` está definida; `RELMEG_REQUER_API_KEY=true` falha o
  startup (fail-fast) em vez de subir exposto;
- **Rate limiting**: slowapi por rota (o disparo de extração tem limite próprio);
- **CORS restritivo**: produção (Vercel) + localhost de desenvolvimento;
- **Blindagem de planilha**: neutralização de fórmulas, rejeição de `.xlsx` quebrados
  (422) e limite de tamanho descomprimido (413, zip-bomb);
- **Privacidade de paths**: rotas expõem apenas o nome do arquivo;
- **Credenciais em `.env`**: nunca commitadas (`.gitignore`).

---

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `RELMEG_API_KEY` | vazio | Chave `X-API-Key`. Se preenchida, todas as rotas exigem o header. |
| `RELMEG_REQUER_API_KEY` | `false` | Se `true` com chave vazia, o startup ABORTA (fail-fast). |
| `CONFIAR_XFF` | `false` | `true` apenas atrás de reverse-proxy controlado. |
| `USA_SCRAPLING` | `true` | Fallback por raspagem (Scrapling) quando a API oficial falha. |
| `SCRAPLING_TIMEOUT_MS` | `45000` | Tolerância do browser headless nas raspagens. |
| `RELMEG_DIR_ENTREGAS` | `~/Desktop/RelMeg - Entregas` | Pasta única de entregas ao cliente. |
| `RELMEG_CACHE_DB` | `backend/data/relmeg_cache.db` | Banco SQLite (cache + auditoria + motor). |
| `RELMEG_LOG_LEVEL` | `INFO` | Nível do loguru. |
| `TSE_ID_ELEICAO_2026` | `20322002026` | id_eleicao para 2026 (**confirmado em 16/09/2026**). |
| `TSE_BASE_URL` | `https://divulgacandcontas.tse.jus.br/divulga/rest/v1` | Base da API do TSE. |
| `TSE_CACHE_TTL` | `86400` | Validade do cache local (segundos; 0 desativa). |
| `HTTP_TIMEOUT`, `HTTP_MAX_TENTATIVAS`, `TSE_MAX_CONCORRENCIA` etc. | ver `config.py` | Retry/backoff/concorrência. |

---

## Testes

```bash
cd RelMEg
backend\.venv\Scripts\python -m pytest tests -q
```

A suíte usa `tempdir` para banco/entregas e URLs de API inválidas (zero rede). Inclui
cobertura de conectores (Câmara, Senado, CLDF), hub, autorias, TSE (cache, reescrita
atômica, detalhe rico) e auditoria.

---

## Licença e autoria

**Criado e mantido por Victor Souza de Aguiar** para a Family Talks.
Uso interno — Family Talks.