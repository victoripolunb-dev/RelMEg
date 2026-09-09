# RelMEg — Plataforma de Monitoramento Legislativo e Inteligência Parlamentar

Plataforma de **monitoramento legislativo e inteligência de stakeholders** desenvolvida para a **Family Talks** (OSCIP dedicada ao fortalecimento da família no Brasil). O sistema consolida dados de múltiplas APIs públicas do governo federal e gera relatórios de inteligência para advocacy, diálogo bipartidário e monitoramento de pautas familiares no Congresso Nacional.

## O que faz

A plataforma atua como um hub centralizado que conecta fontes governamentais dispersas e as transforma em relatórios acionáveis:

### Fontes de dados integradas

| Fonte | Dados coletados |
|-------|----------------|
| **Câmara dos Deputados** | Deputados, proposições, eventos, autores, frentes parlamentares e status legislativo |
| **Senado Federal** | Matérias legislativas, comissões, relatórios e pareceres |
| **TSE** | Dados de candidatos (2018–2026), bens declarados, classificação ocupacional, enriquecimento via API DivulgaCandContas |
| **DOU (Diário Oficial da União)** | Publicações por palavra-chave, data e seção |

### Produtos gerados

1. **Clipping Semanal (.docx)** — Documento Word seguindo modelo específico ("MODELO A SER SEGUIR"), com proposições filtradas pela matriz de inteligência da Family Talks, prontas para envio via WhatsApp
2. **Relatório Executivo (.pdf)** — Relatório corporativo com KPIs, resumos temáticos e tabelas detalhadas (gerado via ReportLab)
3. **Planilha TSE (.xlsx)** — Exportação estruturada de candidatos seguindo o "MODELO BASE" com 25 colunas enriquecidas, compatível com BI (Looker Studio, Google Sheets)

### Matriz de Inteligência Family Talks

Filtro determinístico que classifica proposições contra uma matriz de prioridades temáticas:

**Temas prioritários:** licença parental, proteção infantil, violência familiar, primeira infância, segurança digital, cuidados com idosos

**Temas descartados automaticamente:** lei de divórcio, alienação parental, direito penal, reforma tributária

## Arquitetura

```
RelMEg/
├── backend/
│   ├── main.py                    # App FastAPI, middlewares, registro de rotas
│   ├── config.py                  # Configurações centralizadas (pydantic-settings)
│   ├── database.py                # Persistência SQLite (cache, execuções, auditoria)
│   ├── rate_limit.py              # Rate limiting (slowapi)
│   ├── family_talks.py            # Matriz de inteligência: matching temático + filtro
│   ├── extrator_tse.py            # Extrator/ Enriquecedor TSE + exportador Excel + BackgroundTask
│   ├── modelo_base.py             # Leitor dinâmico de template "MODELO BASE" + formatação Excel
│   ├── exportador_local.py        # Gerador do Clipping Semanal (.docx)
│   ├── exportador_pdf.py          # Gerador de relatório executivo (.pdf)
│   ├── requirements.txt           # Dependências Python (33 pacotes)
│   ├── templates/                 # Templates de documentos
│   │   ├── MODELO A SER SEGUIDO.docx
│   │   └── MODELO BASE
│   └── routers/                   # Módulos de rotas FastAPI
│       ├── deputados.py           # GET /deputados/
│       ├── proposicoes.py         # GET /proposicoes/
│       ├── eventos.py             # GET /eventos/
│       ├── autores.py             # GET /proposicoes/{id}/autores
│       ├── frentes.py             # GET /frentes/
│       ├── monitoramento.py       # GET /monitoramento/camara, /senado
│       ├── dou.py                 # GET /dou/pesquisa
│       ├── tse.py                 # GET /tse/candidatos, /tse/municipios
│       ├── ai.py                  # POST /ai/resumir-dou (POC)
│       ├── fachada.py             # GET /api/camara, /api/senado (fachada unificada)
│       ├── planilha.py            # POST /api/upload/planilha, /api/exportar/sheets
│       ├── auditoria.py           # GET /api/execucoes/historico, /api/execucoes/auditoria
│       └── senado/
│           ├── materias.py        # GET /senado/materias/
│           └── comissoes.py       # GET /senado/comissoes/
├── tests/                         # Suite de testes (pytest)
│   ├── conftest.py
│   ├── test_database.py
│   ├── test_extrator.py
│   ├── test_exportador.py
│   ├── test_exportador_pdf.py
│   └── test_auditoria.py
├── RelMeg - Entregas/             # Entregas para o cliente
├── .env.example                   # Template de variáveis de ambiente
├── .gitignore
└── AGENTS.md                      # Diretrizes arquiteturais
```

## Requisitos

- Python 3.9+
- SQLite (incluso no Python)
- Contas de API governamentais (Câmara, Senado, TSE) — endpoints públicos, sem autenticação

## Instalação

```bash
# Clonar o repositório
git clone https://github.com/victoripolunb-dev/RelMEg.git
cd RelMEg

# Criar ambiente virtual
python -m venv venv
venv\Scripts\activate    # Windows
# source venv/bin/activate  # Linux/Mac

# Instalar dependências
cd backend
pip install -r requirements.txt

# Configurar variáveis de ambiente
cp .env.example .env
# Editar .env com suas configurações
```

## Uso

```bash
# Iniciar o servidor
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Documentação interativa
# http://localhost:8000/docs
```

### Principais endpoints

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/deputados/` | GET | Lista deputados com filtros |
| `/proposicoes/` | GET | Busca proposições com enriquecimento |
| `/eventos/` | GET | Eventos e audiências públicas |
| `/frentes/` | GET | Frentes parlamentares + membros |
| `/senado/materias/` | GET | Matérias do Senado |
| `/senado/comissoes/` | GET | Comissões do Senado |
| `/dou/pesquisa` | GET | Pesquisa no Diário Oficial |
| `/tse/candidatos` | GET | Candidatos com dados enriquecidos |
| `/api/camara` | GET | Fachada unificada Câmara |
| `/api/senado` | GET | Fachada unificada Senado |

## Segurança

- **Autenticação por API Key**: Todas as rotas (exceto `/`, `/docs`, `/redoc`, `/openapi.json`) exigem header `X-API-Key` quando configurado
- **Rate limiting**: Limite de requisição por rota via slowapi (10–120 req/min dependendo do endpoint)
- **CORS restritivo**: Apenas `relmegpina.vercel.app` (produção) e `localhost:5173`/`localhost:8082` (desenvolvimento)
- **Credenciais em `.env`**: Nunca commitadas — o `.gitignore` exclui o arquivo

## Princípio arquitetural

Todas as chamadas a APIs governamentais são **estritamente sob demanda** — disparadas apenas por ações explícitas do usuário (cliques em botões, aplicação de filtros, seleções de datas). Sem cron jobs, sem polling em background. A única exceção é o `BackgroundTask` de extração TSE, que é ele-même disparado por uma requisição HTTP e retorna um task_id para acompanhamento do status.

## Licença

Uso interno — Family Talks.
