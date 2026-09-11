from typing import Dict, List, Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from starlette.requests import Request

from rate_limit import limiter, LIMITE_PROPOSICOES

router = APIRouter(prefix="/eventos", tags=["Eventos e Audiências"])

_URL_BASE = "https://dadosabertos.camara.leg.br/api/v2/eventos"
_HEADERS = {"Accept": "application/json"}
_PADRAO_DATA_ISO = r"^\d{4}-\d{2}-\d{2}$"


@router.get("/", response_model=Dict[str, Any])
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_eventos(
    request: Request,
    dataInicio: Optional[str] = Query(None, pattern=_PADRAO_DATA_ISO, description="Data inicial no formato AAAA-MM-DD"),
    dataFim: Optional[str] = Query(None, pattern=_PADRAO_DATA_ISO, description="Data final no formato AAAA-MM-DD"),
    itens: int = Query(10, ge=1, le=100, description="Quantidade máxima de eventos"),
):
    """Busca agenda de reuniões, audiências públicas e eventos na Câmara."""
    params: Dict[str, Any] = {
        "itens": itens,
        "ordem": "asc",
        "ordenarPor": "dataHoraInicio",
    }
    if dataInicio:
        params["dataInicio"] = dataInicio
    if dataFim:
        params["dataFim"] = dataFim

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            resposta = await client.get(_URL_BASE, params=params, headers=_HEADERS)
            resposta.raise_for_status()
            dados = resposta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível acessar a API de eventos",
        ) from exc

    eventos_formatados: List[Dict[str, Any]] = [
        {
            "id": ev.get("id"),
            "dataHoraInicio": ev.get("dataHoraInicio"),
            "descricao": ev.get("descricao"),
            "descricaoTipo": ev.get("descricaoTipo"),
            "local": ev.get("localSala", "Local não informado"),
        }
        for ev in dados.get("dados", [])
    ]
    return {"total": len(eventos_formatados), "eventos": eventos_formatados}