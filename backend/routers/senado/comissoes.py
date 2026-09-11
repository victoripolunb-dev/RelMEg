from typing import Dict, List, Any

import httpx
from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from rate_limit import limiter, LIMITE_PROPOSICOES

router = APIRouter(prefix="/senado/comissoes", tags=["Senado - Comissões"])

_URL_BASE = "https://legis.senado.leg.br/dadosabertos/comissoes"
_HEADERS = {"Accept": "application/json"}


@router.get("/", response_model=Dict[str, Any])
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_comissoes_senado(request: Request):
    """Busca as comissões ativas no Senado Federal."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            resposta = await client.get(_URL_BASE, headers=_HEADERS)
            resposta.raise_for_status()
            dados = resposta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível acessar a API de comissões do Senado",
        ) from exc

    lista = (dados.get("ListaComissoes", {}) or {}).get("Comissoes", {}) or {}
    comissoes_raw = lista.get("Comissao", [])
    if isinstance(comissoes_raw, dict):
        comissoes_raw = [comissoes_raw]

    comissoes_formatadas: List[Dict[str, Any]] = [
        {
            "codigo": c.get("CodigoComissao"),
            "sigla": c.get("SiglaComissao"),
            "nome": c.get("NomeComissao"),
            "casa": c.get("CasaComissao"),
        }
        for c in comissoes_raw
        if isinstance(c, dict)
    ]
    return {"total": len(comissoes_formatadas), "comissoes": comissoes_formatadas}