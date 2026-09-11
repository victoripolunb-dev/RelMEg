# RelMEg — Tutorial Completo

## 1. O que é o RelMEg?

**RelMEg** (Relatórios de Monitoramento e Engajamento) é um back-end em Python
que coleta dados legislativos de APIs públicas governamentais (Câmara dos
Deputados, Senado Federal, CLDF, DOU, TSE) e gera artefatos de entrega
prontos para clientes de inteligência política/advocacy.

Todo disparo é **sob demanda** (nenhum cron, polling ou varredura em segundo
plano — ver AGENTS.md).

---

## 2. Para que serve?

Gera automaticamente:

| Artefato | Formato | Rota principal |
|----------|---------|----------------|
| **Clipping Semanal** de novas proposições | `.docx` (Word) | `POST /api/exportar/clipping-semanal` |
| **Relatório Executivo** consolidado | `.pdf` | `POST /api/exportar/pdf-executivo` |
| **Planilha de Coleta de Campo** | `.xlsx` (25 colunas) | `GET /hub/proposicoes/{fonte}/{id}/planilha-coleta` |
| **Planilha de Coleta (upload)** | `.xlsx` | `POST /api/planilha` |
| **Extração TSE** (candidatos enriquecidos) | `.xlsx` + cache rico | `GET /tse/exportar/{ano}/{uf}/{cargo}` |
| **Resumo de prováveis efeitos** (IA) | JSON | `POST /ai/resumir-dou` |

---

## 3. Para quem é?

Operador (humano) que usa o sistema via **Postman** ou qualquer cliente HTTP.
Cada ação é um clique explícito no Postman — o back-end reage, coleta,
processa e entrega.

---

## 4. Arquitetura (resumo visual)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Frontend (Vercel)  ──HTTP──>  FastAPI (backend/)                  │
│                                                              │      │
│  ┌──────────────┐    ┌──────────────────────────────────────────┐   │
│  │  main.py     │    │ routers/ (proposicoes, senado, tse, dou, │   │
│  │  (setup,     │    │   ai, fachada, planilha, auditoria, hub) │   │
│  │   CORS,      │    └──────────────────────────────────────────┘   │
│  │   API-key)   │            │                                      │
│  └──────────────┘            ▼                                      │
│                    ┌─────────────────────┐                          │
│                    │  relmeg_core/       │  Conectores por fonte:   │
│                    │  ├─ connectors/     │  camara, senado, cldf,   │
│                    │  ├─ models/         │  dou, algo, almg, alesp  │
│                    │  └─ orquestrador.py │  → roteia fonte→conector │
│                    └─────────────────────┘                          │
│                              │                                      │
│                              ▼                                      │
│                    ┌─────────────────────┐   ┌───────────────────┐  │
│                    │  servicos/          │   │  database.py      │  │
│                    │  ├─ extrator_tse.py │──>│  SQLite (cache,   │  │
│                    │  ├─ exportador_local│   │  execuções TSE,   │  │
│                    │  ├─ exportador_pdf  │   │  proposições,     │  │
│                    │  ├─ exportador_plan.│   │  parlamentares,   │  │
│                    │  ├─ modelo_base.py  │   │  auditoria)       │  │
│                    │  └─ family_talks.py │   └───────────────────┘  │
│                    └─────────────────────┘                          │
│                              │                                      │
│                              ▼                                      │
│                   ~/Desktop/RelMeg - Entregas/                      │
│                   (Word, PDF, XLSX)                                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. Pré-requisitos

- Python 3.9+ (teste com `python --version`)
- NÃO precisa de banco externo (SQLite embutido)
- NÃO precisa de Node/npm (frontend fora do escopo V1)

---

## 6. Setup rápido

```bash
# 1) Entrar na pasta backend
cd backend

# 2) Criar ambiente virtual (opcional, recomendado)
python -m venv .venv
.\.venv\Scripts\activate       # Windows
# source .venv/bin/activate    # Linux/Mac

# 3) Instalar dependências
pip install -r requirements.txt

# 4) Copiar .env.example → .env e ajustar (mínimo obrigatório)
cp ..\.env.example .env        # Windows: copy ..\.env.example .env

# 5) Rodar o servidor
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 6) Abrir a documentação
#    http://localhost:8000/docs    (se RELMEG_DOCS_PUBLICOS=true no .env)
```

Se `RELMEG_API_KEY` estiver vazia, a API sobre sem autenticação (dev).
Em produção, gere uma chave com:
```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 7. Variáveis de ambiente essenciais (.env)

```env
# --- Obrigatórias em produção ---
RELMEG_API_KEY=sua_chave_aqui
RELMEG_REQUER_API_KEY=true

# --- Úteis ---
RELMEG_DOCS_PUBLICOS=true       # abre /docs em dev
DIR_ENTREGAS=C:\Users\...\RelMeg - Entregas
TSE_BASE_URL=https://divulgacandcontas.tse.jus.br/divulga/rest/v1
USA_SCRAPLING=false              # false = não abre browser (recomendado)
```

Consulte `.env.example` para a lista completa (37 opções).

---

## 8. Uso via Postman (roteiro passo a passo)

> Cada chamada abaixo deve ser feita **depois** do servidor estar rodando.
> No Postman, selecione GET ou POST conforme indicado.

### 8.1 Câmara dos Deputados

**Buscar proposições na API (com enriquecimento de situação/relator):**
```
GET http://localhost:8000/proposicoes/?siglaTipo=PL&ano=2026&keywords=familia&itens=20
```

**Coletar e persistir uma proposição específica (task em background):**
```
POST http://localhost:8000/hub/proposicoes/camara/2473
```
→ Retorna `201 Created` com a ficha já salva no repositório local.

**Buscar por palavra-chave e salvar o lote (hub):**
```
GET http://localhost:8000/hub/busca/proposicoes?fonte=camara&termo=familia
```

**Listar proposições salvas no banco local:**
```
GET http://localhost:8000/hub/proposicoes/listar?fonte=camara&ano=2026
```

### 8.2 Senado Federal

**Buscar matérias salvas no hub:**
```
GET http://localhost:8000/hub/busca/proposicoes?fonte=senado&termo=violencia domestica
```

**Listar proposições salvas:**
```
GET http://localhost:8000/hub/proposicoes/listar?fonte=senado
```

### 8.3 Monitoramento Setorial

**Câmara:**
```
GET http://localhost:8000/monitoramento/camara?q=violencia domestica&ano=2026
```

**Senado (busca na ementa):**
```
GET http://localhost:8000/monitoramento/senado?q=violencia domestica&ano=2026
```

Busca é acento-insensível: `violencia` encontra `violência doméstica`.

### 8.4 Diário Oficial da União (DOU)

```
GET http://localhost:8000/dou/pesquisa?q=ANEEL&data=2026-08-28&secao=1
```
→ Retorna `total` + lista de publicações (sob demanda, portal SR).

### 8.5 Clipping Semanal (Word .docx)

**Gerar com filtro Family Talks automático:**
```
POST http://localhost:8000/api/exportar/clipping-semanal
Params: keywords=infancia,idosos&periodo=2026-09-11
```

**Gerar sem filtro (varredura bruta):**
```
POST http://localhost:8000/api/exportar/clipping-semanal?sem_filtro=true
```
→ Retorna JSON com `arquivo` (caminho do .docx entregue).

### 8.6 Relatório Executivo (PDF)

```bash
# Com clipping automático (busca Câmara+Senado+Family Talks):
curl -X POST http://localhost:8000/api/exportar/pdf-executivo \
  -H "Content-Type: application/json" \
  -d '{
    "keywords": "infancia,idosos",
    "titulo": "Relatório Semanal",
    "cliente": "Family Talks"
  }'
```

### 8.7 Planilha de Coleta de Campo

**Gerar planilha de coleta para parlamentares já coletados (filtro por UF/partido):**
```
GET http://localhost:8000/hub/exportar/planilha-coleta?uf=DF&partido=PL&baixar=true
```
→ Grava em `~/Desktop/RelMeg - Entregas/Perfil/` e retorna o nome.

**Upload de planilha preenchida (para validação/conversão):**
```
POST http://localhost:8000/api/upload/planilha
Body: multipart/form-data
  - file: arquivo .csv/.xlsx
  - cliente: identificador (opcional)
```

**Exportar para Google Sheets/CSV:**
```
POST http://localhost:8000/api/exportar/sheets
Body: {"nome": "coleta", "registros": [{...}]}
```

### 8.8 TSE (Extração pesada de candidatos)

**Trigger assíncrono (recomendado):**
```
GET http://localhost:8000/tse/exportar/2026/GO/7
```
→ Retorna `202 Accepted` + `task_id`.

**Acompanhar status:**
```
GET http://localhost:8000/tse/execucoes/{task_id}
```

**Download síncrono (bloqueante):**
```
GET http://localhost:8000/tse/exportar/2026/GO/7?download=true
```

**Detalhe rico de um candidato (cache local):**
```
GET http://localhost:8000/tse/detalhe/{cache_key}/{id_candidato}
```

### 8.9 Hub Legislativo (coleta + leitura)

**Coletar + persistir um projeto específico (ex: PL 2473/2026 da Câmara):**
```
POST http://localhost:8000/hub/proposicoes/camara/2473
```

**Ler tramitações de uma proposição já coletada:**
```
GET http://localhost:8000/hub/proposicoes/camara/2473
```

**Coletar + persistir uma matéria do Senado:**
```
POST http://localhost:8000/hub/proposicoes/senado/12345
```

**Exportar Ficha Legislativa (Word):**
```
GET http://localhost:8000/hub/exportar/ficha?fonte=camara&id_externo=2473
```
→ Retorna `201 Created` com o .docx salvo em `~/Desktop/RelMeg - Entregas/Relatórios/`.
Adicione `&baixar=true` para devolver o arquivo como download.

**Exportar Ficha Parlamentar (Word):**
```
POST http://localhost:8000/hub/exportar/ficha-parlamentar?fonte=camara&id_externo=248166&baixar=true
```

### 8.10 Resumo de Prováveis Efeitos (IA)

```
POST http://localhost:8000/ai/resumir-dou
Body: {
  "titulo": "Reajuste tarifário ANEEL",
  "texto": "A ANEEL reajustou as tarifas de energia em 12% para o período 2026-2028..."
}
```
→ Retorna `{titulo, resumo}`.

### 8.11 Auditoria / Histórico

```
GET http://localhost:8000/api/execucoes/historico?limite=20
```
Alias idêntico:
```
GET http://localhost:8000/api/execucoes/auditoria?limite=20
```

---

## 9. Estrutura de pastas de entrega

Tudo é gravado sob `~/Desktop/RelMeg - Entregas/` (config `DIR_ENTREGAS`):

```
RelMeg - Entregas/
├── TSE/                         # extrações TSE (.xlsx + cache)
├── Novas proposições/           # Clipping Semanal (.docx)
├── Relatórios/                  # Relatórios Executivos PDF
└── Perfil/                      # Planilhas de coleta de campo
```

---

## 10. Como rodar os testes

```bash
# Do diretório raiz do repositório (onde está AGENTS.md)
cd C:\Users\Victor\Desktop\RelMEg

# Rodar todos (zero rede — usa mocks):
python -m pytest tests -q

# Rodar um arquivo específico:
python -m pytest tests/test_extrator.py -q

# Ver cobertura de falhas do TSE:
python -m pytest tests/test_extrator.py::test_404_nao_retenta -v
```

Os testes usam `tempfile` para banco/entregas e `TSE_BASE_URL=http://tse.invalido.invalid/`
— **nenhum teste toca a rede real**.

---

## 11. Regra fundamental (AGENTS.md) — resumo para o operador

1. **Toda ação tem gatilho humano**: clique explícito no Postman.
2. **Nada agenda sozinho**: o back-end não dispara varreduras em background
   (com exceção do trigger `/tse/exportar`, que roda como BackgroundTask
   sob demanda e tem controle de concorrência 409).
3. **Rate limits existem**: cada grupo de rota tem limite próprio (10 a
   120/min). Exceder retorna 429.
4. **Cache é transparente**: se o cache estiver fresco (TSE), o sistema
   retorna os dados locais sem chamar a API externa.

---

## 12. Erros conhecidos e troubleshooting

| Erro HTTP | Causa provável | Solução |
|-----------|---------------|---------|
| 401 | Header `X-API-Key` ausente | Adicione o header no Postman |
| 409 | Extração TSE já em andamento | Acompanhe via `/tse/execucoes/{task_id}` |
| 429 | Rate limit excedido | Aguarde 1 minuto ou reduza a frequência |
| 502 | Fonte externa indisponível | Tente novamente em instantes |
| 503 | Portal DOU instável | Tente novamente ou use outro período |
| 501 | Fonte sem API pública (ex: ALGO) | Consulte `/hub/fontes` para ver disponibilidade |
| 400 | Parâmetros inválidos (UF, cargo, ano) | Confira a tabela de códigos TSE em `/tse/candidatos` |

**Verificar status das fontes:**
```
GET http://localhost:8000/hub/fontes
```

---

## 13. Dicas de uso avançado

- **Para cubrir um PL específico do Senado**: `POST /hub/proposicoes/senado/{id_materia}`
  → Coleta, normaliza e persiste. Depois leia com `GET` na mesma rota.
- **Para exportar múltiplos projetos em lote**: use `/hub/proposicoes/listar`
  para ver o que já tem, depois exporte a ficha de cada um com `/ficha`.
- **Para customizar o Clipping**: envie `keywords` específicas; para ignorar o
  filtro Family Talks, use `sem_filtro=true`.
- **Para testar o sistema sem tocar a rede**: defina `USA_SCRAPLING=false` e
  `TSE_BASE_URL=http://tse.invalido.invalid/` no `.env`.
