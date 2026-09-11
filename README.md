# RelMEg — Plataforma de Monitoramento Legislativo e Inteligência Parlamentar

Plataforma de **monitoramento legislativo e inteligência de stakeholders** desenvolvida para a **Family Talks** (OSCIP dedicada ao fortalecimento da família no Brasil). O sistema consolida dados de múltiplas APIs públicas do governo federal e gera relatórios de inteligência para advocacy, diálogo bipartidário e monitoramento de pautas familiares no Congresso Nacional.

## O que faz

A plataforma atua como um hub centralizado que conecta fontes governamentais dispersas e as transforma em relatórios acionáveis. **Toda coleta é estritamente sob demanda** (ver "Princípio arquitetural").

### Fontes de dados integradas

| Fonte | Dados coletados |
|-------|----------------|
| **Câmara dos Deputados** | Deputados, proposições, eventos, autores, frentes parlamentares, busca por palavras-chave (`keywords`) e status legislativo |
| **Senado Federal** | Matérias legislativas, comissões, relatórios e pareceres |
| **TSE (DivulgaCandContas)** | Candidatos (2018–2026), enriquecimento por candidato e **detalhe rico**: perfil, bens individuais, propostas e redes sociais |
| **CLDF** | Proposições do PLE (DF) via API pública; **histórico de tramitação** via raspagem do portal (a API só expõe a etapa atual) |
| **DOU (Diário Oficial da União)** | **Fonte de busca** (B5): publicações por palavra-chave, data, ano e seção — no hub (`fonte=dou`) e na rota legada `/dou/pesquisa` (mesma lógica no motor) |
| **ALGO** | Esqueleto registrado (fonte reconhecida); responde 501 — sem API pública viável na V1 |
| **ALMG** | Registrada (GET /hub/fontes informa o status); API "Dados Abertos" v2 **existe** — mapeamento futuro (CLDF é o piloto ALE) |
| **ALESP** | Registrada; dados abertos publicados em **CSV/RDF** (bulk) — mapeamento futuro |

### Resiliência (fallback Scrapling — B2/B3)

Quando a API oficial de uma fonte falha (403/503/timeout) ou devolve vazio, o
motor tenta **raspar a página pública** da fonte usando o Scrapling
(`USA_SCRAPLING=true`; instalar `requirements-extras.txt`):

- **Câmara** — ficha de tramitação do portal (`fichadetramitacao`), HTML estável;
- **Senado** — linha do tempo da matéria (extração por datas do texto da página);
- **CLDF** — página "Acompanhar andamento" (renderizada por browser headless),
  entregando o **histórico completo** que a API pública não expõe.

Se o fallback não trouxer dados (markup mudou, página fora do ar), o conector
devolve o que tem (etapa atual / `[]`) **sem nunca levantar** — a extração
segue pela API oficial. Toda raspagem ocorre dentro de uma requisição on-demand
do operador (AGENTS.md); não há varredura agendada.

### Produtos gerados

1. **Clipping Semanal (.docx)** — Documento Word seguindo o modelo "MODELO A SER SEGUIDO.docx", com proposições filtradas pela matriz de inteligência, prontas para envio via WhatsApp.
2. **Relatório Executivo (.pdf)** — Relatório corporativo com KPIs, resumos temáticos e tabelas (ReportLab).
3. **Planilha TSE (.xlsx)** — Exportação estruturada de candidatos seguindo o "MODELO BASE" (25 colunas), compatível com BI (Looker Studio, Google Sheets).
4. **Ficha Legislativa / Ficha de Parlamentar (.docx)** — Dossiê individual de proposição ou de parlamentar, gerado do repositório local.
5. **Dossiê rico TSE (JSON)** — Detalhes máximos por candidato (bens individuais, propostas, redes sociais), persistidos e legíveis sem nova consulta à API.
6. **Planilha de Coleta de Perfil (.xlsx)** — gabarito do **"Modelo base de coleta - Parlamentares.xlsx"** (contrato do operador; cópia interna em `backend/templates/MODELO BASE`) pré-preenchido com Casa/Nome/Partido/UF dos parlamentares salvos no hub; campos de contato/perfil em branco para o trabalho de campo. Grava em `~/Desktop/RelMeg - Entregas/Perfil/`.

### Regra de volume na coleta

- **Sem especificação = coleta o máximo** (ex.: TSE sem `campos` coleta perfil + bens + propostas + redes sociais por candidato).
- **Especificou = restringe** (ex.: `campos=bens,propostas` persiste apenas os blocos pedidos; bloco inválido → 400).

### Matriz de Inteligência Family Talks

Filtro determinístico que classifica proposições contra uma matriz de prioridades temáticas:

- **Temas prioritários:** licença parental, proteção infantil, violência familiar, primeira infância, segurança digital, cuidados com idosos.
- **Temas descartados automaticamente:** lei de divórcio, alienação parental, direito penal, reforma tributária.

## Arquitetura

```
RelMEg/
├── backend/
│   ├── main.py                    # App FastAPI, middlewares, registro de rotas
│   ├── config.py                  # Configurações centralizadas (pydantic-settings)
│   ├── database.py                # Persistência SQLite (cache, execuções, auditoria,
│   │                              #   relmeg_* legislativo, autorias, tse_candidato_detalhe)
│   ├── rate_limit.py              # Rate limiting (slowapi)
│   ├── family_talks.py            # Matriz de inteligência: matching temático + filtro
│   ├── extrator_tse.py            # Extrator/enriquecedor TSE + exportador Excel + trigger
│   │                              #   /tse/exportar + BackgroundTask + leitura do detalhe rico
│   ├── modelo_base.py             # Leitor dinâmico de template "MODELO BASE" + formatação
│   ├── exportador_local.py        # Gerador do Clipping Semanal (.docx) — identidade da casa
│   ├── exportador_pdf.py          # Gerador de relatório executivo (.pdf)
│   ├── relmeg_core/               # Motor universal de inteligência legislativa (hub)
│   │   ├── orquestrador.py        # Roteia fonte canônica → conector
│   │   ├── connectors/            # base_connector, camara, senado, cldf, dou, algo, almg, alesp
│   │   ├── models/schemas.py      # ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel
│   │   ├── exportador_docx.py     # Ficha Legislativa / Ficha de Parlamentar (.docx)
│   │   └── utils/scrapling_engine.py # Fallback de raspagem (estepe; ocioso por padrão)
│   ├── requirements.txt           # Dependências Python (pinadas)
│   ├── requirements-extras.txt    # Scrapling (fallback HTML — opcional)
│   ├── .venv/                     # Ambiente virtual (não versionado)
│   ├── templates/                 # MODELO A SER SEGUIDO.docx, MODELO BASE/
│   └── routers/                   # Módulos de rotas FastAPI
│       ├── tse.py                 # GET /tse/candidatos, /tse/municipios, /tse/candidato/{...}
│       ├── hub.py                 # Hub legislativo: coleta/leitura/exportação .docx + busca
│       ├── planilha.py            # Upload/transformação de planilhas (.xlsx) — blindado
│       ├── auditoria.py           # Histórico e auditoria de execuções
│       ├── deputados.py, proposicoes.py, eventos.py, autores.py, frentes.py
│       ├── monitoramento.py, dou.py, ai.py, fachada.py
│       └── senado/                # materias.py, comissoes.py
├── modelo base/                   # Planilha de referência "Modelo base de coleta - Parlamentares.xlsx"
├── tests/                         # Suíte (pytest), banco/entregas em tempdir (zero rede)
├── RelMeg - Entregas/             # Entregas ao cliente (fora do repositório, ver abaixo)
├── .env.example
├── .gitignore
└── AGENTS.md                      # Diretrizes arquiteturais (obrigatório)
```

## Requisitos

- Python 3.9+
- SQLite (incluso no Python)
- APIs governamentais públicas (Câmara, Senado, TSE, CLDF) — sem autenticação

## Instalação

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

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `RELMEG_API_KEY` | vazio | Chave `X-API-Key`. Se preenchida, todas as rotas exigem o header. |
| `RELMEG_REQUER_API_KEY` | `false` | Se `true` com chave vazia, o startup ABORTA (fail-fast) em vez de subir exposto. |
| `CONFIAR_XFF` | `false` | `true` apenas atrás de reverse-proxy controlado (Vercel/Render/nginx), para o rate limit confiar no 1º `X-Forwarded-For`. |
| `USA_SCRAPLING` | `true` | Fallback por raspagem (Scrapling) quando a API oficial falha/fica vazia; a CLDF usa para buscar o histórico de andamento no portal. |
| `SCRAPLING_TIMEOUT_MS` | `45000` | Tolerância do browser headless nas raspagens sob demanda. |
| `RELMEG_DIR_ENTREGAS` | `~/Desktop/RelMeg - Entregas` | Pasta única de entregas ao cliente (convenção obrigatória). |
| `RELMEG_CACHE_DB` | `backend/data/relmeg_cache.db` | Banco SQLite (cache + auditoria + motor legislativo). |
| `RELMEG_LOG_LEVEL` | `INFO` | Nível do loguru. |
| `RELMEG_CORS_ORIGINS_EXTRA` | vazio | Origens CORS adicionais (além do padrão). |
| `AUDITORIA_RETENCAO_DIAS` | `90` | Retenção do log de auditoria e do detalhe rico TSE (≤0 desativa poda). |
| `TSE_BASE_URL` | `https://divulgacandcontas.tse.jus.br/divulga/rest/v1` | Base da API do TSE. |
| `TSE_CACHE_TTL` | `86400` | Validade do cache local (segundos; 0 desativa). |
| `TSE_ID_ELEICAO_2026` | `2055502026` | **Confirme no portal** (o TSE bloqueou a verificação automática desta rede). |
| `HTTP_TIMEOUT`, `HTTP_MAX_TENTATIVAS`, `TSE_MAX_CONCORRENCIA`, etc. | ver `config.py` | Parâmetros de retry/backoff/concorrência. |

## Uso

```bash
# Iniciar o servidor
cd backend
..\backend\.venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 8000
# Documentação interativa: http://localhost:8000/docs
```

### Principais endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/hub/fontes` | GET | Status das fontes do motor (sem rede). |
| `/hub/busca/proposicoes` | GET | Busca por palavras-chave na fonte (**`camara`** e **`dou`** → 200; outras → 501). Sob demanda. |
| `/hub/proposicoes/listar` | GET | Proposições salvas no repositório local. |
| `/hub/proposicoes/{fonte}/{id}` | GET/POST | Ler salva / disparar coleta (gatilho on-demand). Inclui `autorias`. |
| `/hub/parlamentares/listar` | GET | Parlamentares salvos. |
| `/hub/parlamentares/{fonte}/{id}` | GET/POST | Ler salva (com `proposicoes_autoradas`) / disparar coleta. |
| `/hub/exportar/ficha` | POST | Ficha Legislativa (.docx) de proposição já coletada. |
| `/hub/exportar/ficha-parlamentar` | POST | Ficha de Parlamentar (.docx). |
| `/hub/exportar/planilha-coleta` | POST | Planilha de Coleta de Perfil (.xlsx do MODELO BASE) a partir dos parlamentares salvos. |
| `/tse/exportar/{ano}/{uf}/{cargo}` | GET | **Trigger de extração** (202 + task_id). `campos=` restringe blocos; vazio = máximo. |
| `/tse/execucoes/{task_id}` | GET | Status e log de etapas da extração. |
| `/tse/detalhe/{cache_key}/{id_candidato}` | GET | Dossiê rico do candidato (cache local, sem rede). |
| `/tse/candidatos` | GET | Lista candidatos com filtros avançados. |
| `/tse/candidato/{ano}/{uf}/{id}` | GET | Detalhe ao vivo do TSE (rede). |
| `/deputados/`, `/proposicoes/`, `/eventos/`, `/frentes/` | GET | Câmara (rotas clássicas). |
| `/senado/materias/`, `/senado/comissoes/` | GET | Senado. |
| `/dou/pesquisa` | GET | Pesquisa no Diário Oficial. |

## Entregas ao cliente (convenção obrigatória)

- Pasta única: `~/Desktop/RelMeg - Entregas/` (config `dir_entregas`), em subpastas temáticas (`Relatórios/`, `Novas proposições/`, `TSE/`, `DOU/`, `Dashboards/`, `Perfil/`).
- Relatórios/clippings em **Word (.docx)** com a identidade da casa (Montserrat, texto `333333`, destaques em vermelho `ff0000`).
- Não sobrescrever o template "MODELO A SER SEGUIDO.docx" — sempre gerar a partir dele.

## Segurança

- **API Key**: todas as rotas (exceto `/`, `/docs`, `/redoc`, `/openapi.json`) exigem `X-API-Key` quando `RELMEG_API_KEY` está definida; `RELMEG_REQUER_API_KEY=true` falha o startup se não houver chave.
- **Rate limiting**: slowapi por rota (o disparo de extração tem limite próprio).
- **CORS restritivo**: produção (Vercel) + localhost de desenvolvimento.
- **Blindagem de planilha**: neutralização de fórmulas (`=`, `+`, `-`, `@`), rejeição de `.xlsx` quebrados (422) e limite de tamanho descomprimido (413, zip-bomb).
- **Privacidade de paths**: rotas expõem apenas o nome do arquivo; caminhos absolutos ficam internos.
- **Credenciais em `.env`**: nunca commitadas (`.gitignore`).

## Princípio arquitetural

Todas as chamadas a APIs governamentais são **estritamente sob demanda** (ver `AGENTS.md`): disparadas apenas por ações explícitas do operador na interface — botão de busca, filtro, seleção de data. Proibido cron, polling em background, startup/lifespan que disparam varreduras ou auto-carregamento em abas do frontend.

**Única exceção autorizada:** `BackgroundTask` registrado exclusivamente na rota `/tse/exportar/{ano}/{uf}/{codigo_cargo}` (gatilho do operador → 202 + `task_id` com status em `/tse/execucoes/{task_id}`). Concorrência (`asyncio.gather`) dentro de um handler é permitida — é HTTP concorrente dentro de uma requisição on-demand.

## Testes

```bash
cd RelMEg
backend\.venv\Scripts\python -m pytest tests -q
```

A suíte usa `tempdir` para banco/entregas e URLs de API inválidas (zero rede). Inclui cobertura de conectores (Câmara, Senado, CLDF), hub, autorias, TSE (cache, reescrita atômica, detalhe rico) e auditoria.

## Licença

Uso interno — Family Talks.