"""
Conector da Assembleia Legislativa de Goiás (ALGO) — ESQUELETO documentado.

Status (sondagem em 10/09/2026): a ALGO NÃO publica API de proposições.
O único sistema é o "Alego Digital" (``https://alegodigital.al.go.leg.br/spl``),
um ASP.NET WebForms SPA cujas consultas (consulta-tipo/consulta-cronologico/
consulta-autor...) não expõem controles server-side consultáveis — a interface
é renderizada dinamicamente e exige reverse-engineering de postbacks
(VIEWSTATE/MicrosoftAjax) ou raspagem com headless browser via
``relmeg_core.utils.scrapling_engine``.

Decisão do operador (10/09/2026): CLDF é o piloto ALE da V1. Este módulo fica
como documentação viva + registro canônico da fonte, para retomar quando
houver API oficial ou quando o scraping headless valer o custo.

Superfície mapeada (não consumível hoje, para referência futura):
    - https://alegodigital.al.go.leg.br/spl/consulta-tipo.aspx
    - https://alegodigital.al.go.leg.br/spl/consulta-cronologico.aspx
    - https://alegodigital.al.go.leg.br/spl/consultar-situacao.aspx
    - https://alegodigital.al.go.leg.br/spl/parlamentares.aspx
    - https://transparencia.al.go.leg.br/legislacoes
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from relmeg_core.connectors.base_connector import FonteSemApiPublica, LegislativoConnector
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

URL_BASE = "https://alegodigital.al.go.leg.br/spl"


class AlgoConnector(LegislativoConnector):
    """Conector da Assembléia de Goiás — esqueleto (aguarda API ou scraping headless).

    Todos os métodos levantam ``FonteSemApiPublica`` com orientação clara, para
    que chamadas acidentais não silenciem a ausência de dados estruturados.
    O orquestrador deve marcar esta fonte como ``indisponível`` na V1.
    """

    fonte = "algo"
    url_raiz = URL_BASE
    status_v1 = "indisponível (sem API pública)"

    async def obter_parlamentar(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ParlamentarModel:
        raise FonteSemApiPublica(
            "ALGO: sem API oficial (sistema 'Alego Digital' é ASP.NET WebForms SPA). "
            "Retomar quando houver API ou via scrapling_engine (headless)."
        )

    async def obter_projeto(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ProjetoDeLeiModel:
        raise FonteSemApiPublica(
            "ALGO: sem API oficial de proposições. "
            "Superfície mapeada em backend/relmeg_core/connectors/algo.py."
        )

    async def obter_tramitacoes(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> List[TramitacaoModel]:
        raise FonteSemApiPublica(
            "ALGO: sem API oficial de tramitação. "
            "Superfície mapeada em backend/relmeg_core/connectors/algo.py."
        )