"""
Conector da Assembleia Legislativa de São Paulo (ALESP) — mapeamento futuro.

Status (sondagem em 10/09/2026): a ALESP NÃO publica API JSON de proposições
no padrão REST. O que existe:

- "Portal Dados Abertos ALESP" (``https://www.al.sp.gov.br/bases/``): arquivos
  abertos de **Proposições e Processos** e **Legislação** em **RDF/CSV** (bulk);
- Portal SPLE (processo legislativo eletrônico, ``https://sempapel.al.sp.gov.br/spl``),
  interface de consulta por situação/tipo/autor;
- Web service SOAP legado (``https://splegisws.saopaulo.sp.leg.br/ws/ws2.asmx``,
  ex.: ``ProjetosPorAno``) — XML/SOAP, fora do padrão REST do motor.

Decisão do operador (10/09/2026): CLDF é o piloto ALE da V1; ALESP fica como
mapeamento futuro. Quando retomado, o caminho natural é ingerir os CSVs
(proposições + tramitações multivaloradas) sob demanda do operador e indexar a
busca localmente — nunca download programado (AGENTS.md).
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from relmeg_core.connectors.base_connector import FonteSemApiPublica, LegislativoConnector
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

URL_BASE = "https://www.al.sp.gov.br/bases"


class AlespConnector(LegislativoConnector):
    """Conector da Assembleia de São Paulo — mapeamento futuro (dados em CSV/RDF).

    Proposições e processos são publicados como arquivos abertos (bulk), não
    como API JSON consultável por item. Chamadas acidentais levantam
    ``FonteSemApiPublica`` com a orientação correta.
    """

    fonte = "alesp"
    url_raiz = URL_BASE
    status_v1 = "dados em arquivos (CSV/RDF) — mapeamento futuro"

    async def obter_parlamentar(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ParlamentarModel:
        raise FonteSemApiPublica(
            "ALESP: sem API JSON por item — dados abertos em CSV/RDF "
            "(https://www.al.sp.gov.br/bases/); ingerir sob demanda. "
            "CLDF é o piloto ALE da V1. Detalhes em backend/relmeg_core/connectors/alesp.py."
        )

    async def obter_projeto(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ProjetoDeLeiModel:
        raise FonteSemApiPublica(
            "ALESP: sem API JSON por item — dados abertos em CSV/RDF "
            "(https://www.al.sp.gov.br/bases/); ingerir sob demanda. "
            "CLDF é o piloto ALE da V1. Detalhes em backend/relmeg_core/connectors/alesp.py."
        )

    async def obter_tramitacoes(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> List[TramitacaoModel]:
        raise FonteSemApiPublica(
            "ALESP: sem API JSON por item — dados abertos em CSV/RDF "
            "(https://www.al.sp.gov.br/bases/); ingerir sob demanda. "
            "CLDF é o piloto ALE da V1. Detalhes em backend/relmeg_core/connectors/alesp.py."
        )