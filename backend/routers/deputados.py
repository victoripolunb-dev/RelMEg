from typing import List, Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from starlette.requests import Request

from rate_limit import limiter, LIMITE_PROPOSICOES

router = APIRouter(prefix="/deputados", tags=["Deputados"])

_URL_BASE = "https://dadosabertos.camara.leg.br/api/v2/deputados"
_HEADERS = {"Accept": "application/json"}


@router.get("/", response_model=Dict[str, Any])
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_deputados(
    request: Request,
    itens: int = Query(3, ge=1, le=100, description="Quantidade máxima de deputados"),
):
    """Busca os primeiros parlamentares na API oficial da Câmara dos Deputados."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            resposta = await client.get(_URL_BASE, params={"itens": itens}, headers=_HEADERS)
            resposta.raise_for_status()
            dados = resposta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível acessar a API da Câmara dos Deputados",
        ) from exc

    deputados_formatados: List[Dict[str, Any]] = [
        {
            "nome": dep.get("nome"),
            "partido": dep.get("siglaPartido"),
            "uf": dep.get("siglaUf"),
        }
        for dep in dados.get("dados", [])
    ]
    return {"total": len(deputados_formatados), "deputados": deputados_formatados}