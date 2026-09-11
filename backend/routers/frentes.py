from typing import Dict, List, Any

import httpx
from fastapi import APIRouter, HTTPException, Path, Query
from starlette.requests import Request

from rate_limit import limiter, LIMITE_PROPOSICOES

router = APIRouter(prefix="/frentes", tags=["Frentes Parlamentares"])

_URL_BASE = "https://dadosabertos.camara.leg.br/api/v2/frentes"
_HEADERS = {"Accept": "application/json"}


@router.get("/", response_model=Dict[str, Any])
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_frentes(
    request: Request,
    itens: int = Query(10, ge=1, le=100, description="Quantidade máxima de frentes retornadas"),
):
    """Busca as frentes parlamentares ativas na Câmara dos Deputados."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            resposta = await client.get(_URL_BASE, params={"itens": itens}, headers=_HEADERS)
            resposta.raise_for_status()
            dados = resposta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível acessar a API de frentes parlamentares",
        ) from exc

    frentes_formatadas: List[Dict[str, Any]] = [
        {
            "id": f.get("id"),
            "titulo": f.get("titulo"),
            "idLegislatura": f.get("idLegislatura"),
        }
        for f in dados.get("dados", [])
    ]
    return {"total": len(frentes_formatadas), "frentes": frentes_formatadas}


@router.get("/{id}/membros", response_model=Dict[str, Any])
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_membros_frente(
    request: Request,
    id: int = Path(..., ge=1, description="ID da frente parlamentar"),
):
    """Busca os deputados membros de uma frente parlamentar específica pelo ID."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            resposta = await client.get(f"{_URL_BASE}/{id}/membros", headers=_HEADERS)
            resposta.raise_for_status()
            dados = resposta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível buscar os membros desta frente",
        ) from exc

    membros_formatados: List[Dict[str, Any]] = [
        {
            "nome": m.get("nome"),
            "cargo": m.get("titulo"),
            "partido": m.get("siglaPartido"),
            "uf": m.get("siglaUf"),
        }
        for m in dados.get("dados", [])
    ]
    return {
        "frente_id": id,
        "total_membros": len(membros_formatados),
        "membros": membros_formatados,
    }