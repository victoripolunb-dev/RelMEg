"""
Conector da Assembleia Legislativa de Minas Gerais (ALMG) — mapeamento futuro.

Status (sondagem em 10/09/2026): a ALMG MANTÉM uma API pública consolidada —
"Dados Abertos ALMG" v2 (``https://dadosabertos.almg.gov.br/api/v2/...``),
com endpoints de proposições em tramitação (desde 1959), comissões,
deputados, pronunciamentos e orçamento, além de arquivos CSV de proposições e
tramitações (dados multivalorados juntáveis pelo ``CodigoProposicao``).

Decisão do operador (10/09/2026): CLDF é o piloto ALE da V1; ALMG fica como
mapeamento futuro registrado. Quando retomado, o conector deve espelhar o
padrão do ``CldfConnector`` (busca via API de dados abertos + fallback de
raspagem se preciso), normalizando para os mesmos modelos.

Superfície mapeada (para referência futura):
    - https://dadosabertos.almg.gov.br/api/v2/  (base dos endpoints)
    - https://dadosabertos.almg.gov.br/          (portal/mapa de recursos)
    - https://dadosabertos.almg.gov.br/documentacao/arquivos  (CSVs: proposições,
      tramitação multivalorada, legislação, diário do legislativo)
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from relmeg_core.connectors.base_connector import FonteSemApiPublica, LegislativoConnector
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

URL_BASE = "https://dadosabertos.almg.gov.br/api/v2"


class AlmgConnector(LegislativoConnector):
    """Conector da Assembleia de Minas — mapeamento futuro (API dadosabertos v2).

    A API pública existe e está documentada; o mapeamento ainda não foi
    implementado na V1 (CLDF é o piloto ALE aprovado). Chamadas acidentais
    levantam ``FonteSemApiPublica`` com a orientação correta, para que a
    ausência de dados não passe despercebida.
    """

    fonte = "almg"
    url_raiz = URL_BASE
    status_v1 = "mapeamento futuro (API dadosabertos v2)"

    async def obter_parlamentar(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ParlamentarModel:
        raise FonteSemApiPublica(
            "ALMG: API 'Dados Abertos' v2 publicada em https://dadosabertos.almg.gov.br/api/v2 — "
            "mapeamento ainda não implementado na V1 (CLDF é o piloto ALE). "
            "Superfície mapeada em backend/relmeg_core/connectors/almg.py."
        )

    async def obter_projeto(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ProjetoDeLeiModel:
        raise FonteSemApiPublica(
            "ALMG: API 'Dados Abertos' v2 publicada em https://dadosabertos.almg.gov.br/api/v2 — "
            "mapeamento ainda não implementado na V1 (CLDF é o piloto ALE). "
            "Superfície mapeada em backend/relmeg_core/connectors/almg.py."
        )

    async def obter_tramitacoes(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> List[TramitacaoModel]:
        raise FonteSemApiPublica(
            "ALMG: API 'Dados Abertos' v2 publicada em https://dadosabertos.almg.gov.br/api/v2 — "
            "mapeamento ainda não implementado na V1 (CLDF é o piloto ALE). "
            "Superfície mapeada em backend/relmeg_core/connectors/almg.py."
        )