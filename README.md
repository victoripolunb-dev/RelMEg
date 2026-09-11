# RelMeg — Plataforma de Monitoramento Legislativo e Inteligência Parlamentar

## Visão Geral

**RelMeg** é uma plataforma completa de monitoramento legislativo e inteligência de stakeholders desenvolvida para a **Family Talks** (OSCIP dedicada ao fortalecimento da família no Brasil). O sistema consolida dados de múltiplas APIs públicas do governo federal e gera relatórios de inteligência para advocacy, diálogo bipartidário e monitoramento de pautas familiares no Congresso Nacional.

Atua como um hub centralizado que conecta fontes governamentais dispersas e as transforma em relatórios acionáveis. **Toda coleta é estritamente sob demanda** — ver "Princípio Arquitetural" abaixo.

## O Que Faz

### Fontes de Dados Integradas

| Fonte | Dados Coletados |
|-------|----------------|
| **Câmara dos Deputados** | Deputados, proposições, eventos, autores, frentes parlamentares, busca por palavras-chave (`keywords`) e status legislativo |
| **Senado Federal** | Matérias legislativas, comissões, relatórios e pareceres |
| **TSE (DivulgaCandContas)** | Candidatos (2018–2026), enriquecimento por candidato e **detalhe rico**: perfil, bens individuais, propostas e redes sociais |
| **CLDF** | Proposições do PLE (DF) via API pública; **histórico de tramitação** via raspagem do portal (a API só expõe a etapa atual) |
| **DOU (Diário Oficial da União)** | **Fonte de busca** (B5): publicações por palavra-chave, data, ano e seção — no hub (`fonte=dou`) e na rota legada `/dou/pesquisa` (mesma lógica no motor) |
| **ALGO** | Esqueleto registrado (fonte reconhecida); responde 501 — sem API pública viável na V1 |
| **ALMG** | Registrada (GET /hub/fontes informa o status); API "Dados Abertos" v2 **existe** — mapeamento futuro (CLDF é o piloto ALE) |
| **ALESP** | Registrada; dados abertos publicados em **CSV/RDF** (bulk) — mapeamento futuro |

### Resiliência (Fallback Scrapling — B2/B3)

Quando a API oficial de uma fonte falha (403/503/timeout) ou devolve vazio, o motor tenta **raspar a página pública** da fonte usando o Scrapling (`USA_SCRAPLING=true`; instalar `requirements-extras.txt`):

- **Câmara** — ficha de tramitação do portal (`fichadetramitacao`), HTML estável;
- **Senado** — linha do tempo da matéria (extração por datas do texto da página);
- **CLDF** — página "Acompanhar andamento" (renderizada por browser headless), entregando o **histórico completo** que a API pública não expõe.

Se o fallback não trouxer dados (markup mudou, página fora do ar), o conector devolve o que tem (etapa atual / `[]`) **sem jamais levantar** — a extração segue pela API oficial. Toda raspagem ocorre dentro de uma requisição on-demand do operador (AGENTS.md); não há varredura agendada.

### Produtos Gerados

1. **Clipping Semanal (.docx)** — Documento Word seguindo o modelo "MODELO A SER SEGUIDO.docx", com proposições filtradas pela matriz de inteligência, prontas para envio via WhatsApp.
2. **Relatório Executivo (.pdf)** — Relatório corporativo com KPIs, resumos temáticos e tabelas (ReportLab).
3. **Planilha TSE (.xlsx)** — Exportação estruturada de candidatos seguindo o "MODELO BASE" (25 colunas), compatível com BI (Looker Studio, Google Sheets).
4. **Ficha Legislativa / Ficha de Parlamentar (.docx)** — Dossiê individual de proposição ou de parlamentar, gerado do repositório local.
5. **Dossiê Rico TSE (JSON)** — Detalhes máximos por candidato (perfil, **bens individuais + agregados** — maior/menor bem e distribuição por tipo —, **propostas estruturadas** e **redes sociais com plataforma**), persistidos e legíveis sem nova consulta à API.
6. **Planilha de Coleta de Perfil (.xlsx)** — gabarito do **"Modelo base de coleta - Parlamentares.xlsx"** (contrato do operador; cópia interna em `backend/templates/MODELO BASE`) pré-preenchido com Casa/Nome/Partido/UF dos parlamentares salvos no hub; campos de contato/perfil em branco para o trabalho de campo. Grava em `~/Desktop/RelMeg - Entregas/Perfil/`.

### Regra de Volume na Coleta

- **Sem especificação = coleta o máximo** (ex.: TSE sem `campos` coleta perfil + bens + propostas + redes sociais por candidato).
- **Especificou = restringe** (ex.: `campos=bens,propostas` persiste apenas os blocos pedidos; bloco inválido → 400).

### Matriz de Inteligência Family Talks

Filtro determinístico que classifica proposições contra uma matriz de prioridades temáticas:

- **Temas prioritários:** licença parental, proteção infantil, violência familiar, primeira infância, segurança digital, cuidados com idosos.
- **Temas descartados automaticamente:** lei de divórcio, alienação parental, direito penal, reforma tributária.

## Princípio Arquitetural

**Todas as chamadas a APIs governamentais são estritamente sob demanda** (ver AGENTS.md): disparadas apenas por ações explícitas do operador na interface — botão de busca, filtro, seleção de data. Proibido cron, polling em background, startup/lifespan que disparam varreduras ou auto-carregamento em abas do frontend.

### Única Exceção Autorizada: `BackgroundTasks` em extração pesada sob demanda

Aprovada em 07/09/2026 pelo operador ("Revisar AGENTS.md e implementar"), com requisitos rígidos:

- **Disparo exclusivo por requisição HTTP explícita**: a `BackgroundTask` é registrada APENAS dentro da rota `/tse/exportar/{ano}/{uf}/{codigo_cargo}` (o próprio trigger on-demand) e retorna 202 + `task_id`, com status em `GET /tse/execucoes/{task_id}` e log de etapas persistido em `backend/database.py`.
- A tarefa registrada deve baixar o payload inicial no próprio handler, e a varredura de enriquecimento não reutiliza caches após a falha da primeira chamada — sempre passando pelo cache SQLite local.
- **Nunca** agendar, cron, startup/lifespan, dispatcher automático ou polling de fila. O worker é inerte sem a requisição do operador.
- Manter os rate limits (slowapi) na rota de disparo, inclusive durante o processamento em background.
- Recusar (400/409) disparo de segunda tarefa concorrente para os mesmos filtros enquanto uma execução do mesmo escopo estiver "Executando".

## Entregas ao Cliente (Convenção Obrigatória — Aprovada em 07/09/2026)

- **Pasta de entregas única**: todo artefato entregue ao cliente (relatórios, clippings, planilhas, extratos) deve ser gravado sob `~/Desktop/RelMeg - Entregas/` (config: `settings.dir_entregas`), em subpasta temática (`Relatórios/`, `Novas proposições/`, `TSE/`, `DOU/`, `Dashboards/`, `Perfil/`). Nunca deixar entregas na raiz do repositório.
- **Formato padrão Word (.docx)** para relatórios e clippings — gerar com python-docx reutilizando a identidade da casa (fonte Montserrat, texto `333333`, links de destaque em negrito vermelho `ff0000`, como em `backend/exportador_local.py`). Arquivos de apoio (JSON/Excel) podem acompanhar o .docx na mesma subpasta.
- Validação: o template `MODELO A SER SEGUIDO.docx` não deve ser sobrescrito; sempre clonar/gemar a partir dele quando aplicável (`exportador_local.py`).

## Segurança

- **API Key**: todas as rotas (exceto `/`, `/docs`, `/redoc`, `/openapi.json`) exigem `X-API-Key` quando `RELMEG_API_KEY` está definida; `RELMEG_REQUER_API_KEY=true` falha o startup (fail-fast) em vez de subir exposto.
- **Rate limiting**: slowapi por rota (o disparo de extração tem limite próprio).
- **CORS restritivo**: produção (Vercel) + localhost de desenvolvimento.
- **Blindagem de planilha**: neutralização de fórmulas (`=`, `+`, `-`, `@`), rejeição de `.xlsx` quebrados (422) e limite de tamanho descomprimido (413, zip-bomb).
- **Privacidade de paths**: rotas expõem apenas o nome do arquivo; caminhos absolutos ficam internos.
- **Credenciais em `.env`**: nunca commitadas (`.gitignore`).

## Rotas Principais

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
| `/tse/exportar/{ano}/{uf}/{cargo}` | GET | **Trigger de extração** (202 + task_id). `campos=` restringe blocos; vazio = máximo. |
| `/tse/execucoes/{task_id}` | GET | Status e log de etapas da extração. |
| `/tse/detalhe/{cache_key}/{id_candidato}` | GET | Dossiê rico do candidato (cache local, sem rede). |
| `/tse/candidatos` | GET | Lista candidatos com filtros avançados. |
| `/tse/candidato/{ano}/{uf}/{id}` | GET | Detalhe ao vivo do TSE (rede). |
| `/deputados/`, `/proposicoes/`, `/eventos/`, `/frentes/` | GET | Câmara (rotas clássicas). |
| `/senado/materias/`, `/senado/comissoes/` | GET | Senado. |
| `/dou/pesquisa` | GET | Pesquisa no Diário Oficial. |

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

## Variáveis de Ambiente

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

## Testes

```bash
cd RelMEg
backend\.venv\Scripts\python -m pytest tests -q
```

A suíte usa `tempdir` para banco/entregas e URLs de API inválidas (zero rede). Inclui cobertura de conectores (Câmara, Senado, CLDF), hub, autorias, TSE (cache, reescrita atômica, detalhe rico) e auditoria.

## Licença

Uso interno — Family Talks.