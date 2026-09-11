"""Pacote de serviços de geração de entregas (backend/servicos).

Agrupa os módulos de produção de artefatos finais ao cliente: extrator do TSE
(extrator_tse), Clipping e relatório em Word (exportador_local), relatório
executivo em PDF (exportador_pdf), planilha de coleta de campo
(exportador_planilha), o contrato/modelo base dinâmico (modelo_base) e a matriz
de inteligência Family Talks (family_talks).

Disparo sempre sob demanda (AGENTS.md): nenhum desses módulos agenda, cron ou
varre em segundo plano — são acionados por requisição explícita do operador.
"""