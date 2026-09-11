from typing import Dict, List, Any

import httpx
from fastapi import APIRouter, HTTPException, Path
from starlette.requests import Request

from rate_limit import limiter, LIMITE_PROPOSICOES

router = APIRouter(prefix="/autores", tags=["Autores e Relatores"])

_URL_BASE = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
_HEADERS = {"Accept": "application/json"}


@router.get("/{id}/autores", response_model=Dict[str, Any])
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_autores_proposicao(
    request: Request,
    id: int = Path(..., ge=1),
):
    """Busca os autores de uma proposição específica pelo ID."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            resposta = await client.get(f"{_URL_BASE}/{id}/autores", headers=_HEADERS)
            resposta.raise_for_status()
            dados = resposta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível buscar os autores desta proposição",
        ) from exc

    autores_formatados: List[Dict[str, Any]] = [
        {
            "nome": autor.get("nome"),
            "tipo": autor.get("tipo"),
            "partido": autor.get("siglaPartido"),
            "uf": autor.get("siglaUf"),
        }
        for autor in dados.get("dados", [])
    ]
    return {
        "proposicao_id": id,
        "total_autores": len(autores_formatados),
        "autores": autores_formatados,
    }