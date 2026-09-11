from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from starlette.requests import Request

from rate_limit import limiter, LIMITE_DOU
from relmeg_core.connectors.dou import (
    MAX_ITENS as _ITENS_MAX,
    coletar_portal_sr,
    url_busca_oficial as _url_busca_oficial,
)

router = APIRouter(prefix="/dou", tags=["Diário Oficial da União (DOU)"])


@router.get("/pesquisa")
@limiter.limit(LIMITE_DOU)
def pesquisar_dou(
    request: Request,
    q: str = Query(..., min_length=3, max_length=150, description="Termo de busca no DOU, ex: ANEEL, concessão, marco regulatório"),
    data: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Data da publicação no formato AAAA-MM-DD (ex: 2026-08-28). Se vazio, busca no último mês."),
    secao: int = Query(1, ge=1, le=3, description="Seção do DOU: 1 (leis/atos normativos), 2 (pessoal), 3 (contratos/editais)"),
    itens: int = Query(20, ge=1, le=_ITENS_MAX, description="Quantidade máxima de resultados coletados (paginados automaticamente)"),
):
    """
    Busca publicações no Diário Oficial da União (DOU) filtrando por termo, data e seção.

    Motor migrado (em 2026): a API JSON legada da Imprensa Nacional foi
    descontinuada; o endpoint agora varre o motor de busca SR do portal
    ato-a-aato (https://www.in.gov.br/consulta/-/buscar/dou), de forma
    on-demand (AGENTS.md), delegando a lógica ao conector
    ``relmeg_core.connectors.dou`` (mesma fonte de verdade do hub).

    Erro conhecido de integração (2026): a API JSON legada
    (https://in.gov.br/es/web/dou/-/api/json) foi descontinuada pela Imprensa
    Nacional (retorna 404). Esta rota consome o motor de busca do próprio portal
    (Liferay SR — busca ato-a-ato), que embute os resultados no HTML de forma
    estável e sob demanda — do qual `/hub/busca/proposicoes?fonte=dou` é a
    versão normalizada no hub.
    """
    try:
        resultados = [
            {k: v for k, v in hit.items() if k not in ("fonte", "id_externo")}
            for hit in coletar_portal_sr(q, secao=secao, data=data, itens=itens)
        ]
    except Exception as exc:  # noqa: BLE001 — falha de integração do portal → 503
        logger.error(
            "dou: falha ao coletar o portal SR — {tipo}: {e}",
            tipo=type(exc).__name__, e=exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Não foi possível recarregar os resultados do DOU deste período. "
                "O portal pode estar instável — tente novamente em instantes ou "
                "use a busca oficial."
            ),
        ) from exc

    if not resultados:
        return {
            "termo_pesquisado": q,
            "data_filtro": data or "Mais recentes / Sem data fixa",
            "secao": secao,
            "total": 0,
            "resultados": [],
            "url_busca_oficial": _url_busca_oficial(q, secao, data),
        }

    return {
        "termo_pesquisado": q,
        "data_filtro": data or "Mais recentes / Sem data fixa",
        "secao": secao,
        "total": len(resultados),
        "resultados": [r for r in resultados],
        "url_busca_oficial": _url_busca_oficial(q, secao, data),
    }