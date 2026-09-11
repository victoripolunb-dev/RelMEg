"""
relmeg_core — Motor de inteligência legislativa (V1).

Hub universal de extração normalizada (Câmara, Senado, DOU e ALEs) que
traduz qualquer fonte — JSON oficial ou HTML raspado — para os Modelos
Pydantic definidos em ``relmeg_core.models.schemas``.

Princípios (alinhados ao AGENTS.md):
    - Execução estritamente sob demanda: nenhum conector é acionado sem um
      pedido explícito do operador (routers/FastAPI).
    - Adapter: o banco/receptor só conhece os modelos normalizados, nunca o
      formato da fonte.
    - Fallback opcional e lazy: o Scrapling é importado apenas no caminho de
      contingência, nunca no import do pacote.
"""