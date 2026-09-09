# AGENTS.md — Diretrizes arquiteturais do RelMeg

## Regra inegociável: execução estritamente sob demanda

Todas as varreduras, pesquisas e requisições a APIs externas — Câmara dos
Deputados, Senado Federal, Diário Oficial da União (DOU) e TSE — devem ocorrer
**estritamente sob demanda**.

### É terminantemente proibido

- Rotinas de varredura invisíveis em segundo plano;
- *Cron jobs* e tarefas agendadas (inclusive a chave `"crons"` do `vercel.json`);
- Qualquer *polling* automático ou biblioteca de processamento em *background*
  (`BackgroundTasks`, `APScheduler`, Celery, threads/daemons, handlers de
  `startup`/`lifespan` que disparem varreduras, loops com `while True`, etc.);
- Consumo autônomo das APIs governamentais sem ação do usuário;
- Auto-carregamento de varredura na montagem ou em qualquer ciclo de vida de
  componentes no frontend (ex.: `autoCarregarVazio`, refs de "carregar uma
  única vez ao abrir a aba", `useEffect` de montagem que dispare busca). Abrir
  uma página/aba **não** é uma ação explícita de busca.

### O gatilho é sempre o usuário

O motor do sistema só pode ser acionado por uma ação direta e intencional do
operador na interface, como:

- Aplicação de um filtro;
- Seleção de um cliente específico;
- Definição de uma data no calendário do DOU;
- Clique expresso em um botão de busca.

A interface deve aguardar passivamente o comando e exibir estados de carregamento
visual apenas durante a requisição ativa, entregando os resultados em tempo real
após a conclusão desse processamento pontual.

### Justificativa

Proteção absoluta contra o esgotamento dos *rate limits* das APIs públicas
governamentais (evitando bloqueios institucionais), prevenção do consumo
desnecessário de recursos e do sobrecarregamento do servidor.

### Notas de conformidade

- `asyncio.gather` dentro de um handler é permitido: trata-se apenas de concorrência
  de chamadas HTTP dentro de uma requisição on-demand, e não de processamento em
  segundo plano.
- Não adicionar `BackgroundTasks` nem handlers de `startup`/`lifespan` que efetuem
  consultas a APIs externas.
- Não adicionar a chave `"crons"` em nenhum `vercel.json` do repositório.
- Não introduzir `setInterval`/polling no frontend que dispare requisições sem
  interação explícita do usuário.
- Abas/páginas com dados vazios devem apenas orientar o operador a usar o botão
  de busca/atualização — nunca disparar a varredura sozinhas.

### Exceção autorizada: `BackgroundTasks` em extração pesada sob demanda

Aprovada em 07/09/2026 pelo operador ("Revisar AGENTS.md e implementar"),
com requisitos rígidos:

- **Disparo exclusivo por requisição HTTP explícita**: a `BackgroundTask` é
  registrada APENAS dentro da rota `/tse/exportar/{ano}/{uf}/{codigo_cargo}`
  (o próprio trigger on-demand) e retorna 202 + `task_id`, com status em
  `GET /tse/execucoes/{task_id}` e log de etapas persistido em `backend/database.py`.
- A tarefa registrada deve baixar o payload inicial no próprio handler, e a
  varredura de enriquecimento não reutiliza caches após a falha da primeira
  chamada — sempre passando pelo cache SQLite local.
- **Nunca** agendar, cron, startup/lifespan, dispatcher automático ou polling
  de fila. O worker é inerte sem a requisição do operador.
- Manter os rate limits (slowapi) na rota de disparo, inclusive durante o
  processamento em background.
- Recusar (400/409) disparo de segunda tarefa concorrente para os mesmo filtros
  enquanto uma execução do mesmo escopo estiver "Executando".

Nenhuma outra rota, cli ou serviço pode invocar `BackgroundTasks` sem nova
revisão e aprovação explícita deste documento.

## Entregas ao cliente (convenção obrigatória — aprovada em 07/09/2026)

- **Pasta de entregas única**: todo artefato entregue ao cliente (relatórios,
  clippings, planilhas, extratos) deve ser gravado sob
  `~/Desktop/RelMeg - Entregas/` (config: `settings.dir_entregas`),
  em subpasta temática (`Relatórios/`, `Novas proposições/`, `TSE/`, `DOU/`,
  `Dashboards/`, `Perfil/`). Nunca deixar entregas na raiz do repositório.
- **Formato padrão Word (.docx)** para relatórios e clippings — gerar com
  python-docx reutilizando a identidade da casa (fonte Montserrat, texto
  `333333`, links de destaque em negrito vermelho `ff0000`, como em
  `backend/exportador_local.py`). Arquivos de apoio (JSON/Excel) podem
  acompanhar o .docx na mesma subpasta.
- Validação: o template `MODELO A SER SEGUIDO.docx` não deve ser sobrescrito;
  sempre clonar/gemar a partir dele quando aplicável (`exportador_local.py`).