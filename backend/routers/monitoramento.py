from datetime import date
import unicodedata
from typing import Dict, List, Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from starlette.requests import Request

from rate_limit import limiter, LIMITE_MONITORAMENTO

router = APIRouter(prefix="/monitoramento", tags=["Monitoramento Setorial"])

_URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
_URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"
_HEADERS = {"Accept": "application/json"}


def _normalizar_acentos(texto: Optional[str]) -> str:
    """Remove acentos/acentuação do texto para busca case/acento-insensível."""
    if not texto:
        return ""
    norm = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in norm if unicodedata.category(c) != "Mn").lower()


@router.get("/camara", response_model=Dict[str, Any])
@limiter.limit(LIMITE_MONITORAMENTO)
async def monitorar_camara(
    request: Request,
    q: str = Query(..., min_length=3, max_length=120, description="Palavra-chave para buscar nas ementas, ex: energia, tarifa, marco regulatório"),
    ano: int = Query(date.today().year, ge=1900, le=2100, description="Ano das proposições"),
    itens: int = Query(10, ge=1, le=50, description="Quantidade máxima de resultados"),
):
    """Busca proposições na Câmara filtrando por palavras-chave na ementa ou texto."""
    params: Dict[str, Any] = {
        "ano": ano,
        "keywords": q,
        "itens": itens,
        "ordem": "desc",
        "ordenarPor": "id",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            resposta = await client.get(_URL_CAMARA, params=params, headers=_HEADERS)
            resposta.raise_for_status()
            dados = resposta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível realizar o monitoramento na Câmara",
        ) from exc

    proposicoes_formatadas: List[Dict[str, Any]] = [
        {
            "id": p.get("id"),
            "uri": p.get("uri"),
            "siglaTipo": p.get("siglaTipo"),
            "numero": p.get("numero"),
            "ano": p.get("ano"),
            "ementa": p.get("ementa"),
        }
        for p in dados.get("dados", [])
    ]
    return {
        "termo_pesquisado": q,
        "total": len(proposicoes_formatadas),
        "resultados": proposicoes_formatadas,
    }


@router.get("/senado", response_model=Dict[str, Any])
@limiter.limit(LIMITE_MONITORAMENTO)
async def monitorar_senado(
    request: Request,
    q: str = Query(..., min_length=3, max_length=120, description="Palavra-chave para buscar nas matérias do Senado, ex: energia, marco legal"),
    ano: Optional[int] = Query(date.today().year, ge=1900, le=2100, description="Ano da matéria"),
):
    """Busca matérias no Senado filtrando por palavra-chave na ementa."""
    params: Dict[str, Any] = {}
    if ano:
        params["ano"] = ano

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            resposta = await client.get(_URL_SENADO, params=params, headers=_HEADERS)
            resposta.raise_for_status()
            dados = resposta.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível acessar a API de monitoramento do Senado",
        ) from exc

    pesquisa = dados.get("PesquisaBasicaMateria", {}) or {}
    lista_materias = (pesquisa.get("Materias", {}) or {}).get("Materia", [])
    if isinstance(lista_materias, dict):
        lista_materias = [lista_materias]

    termo = _normalizar_acentos(q)
    filtradas: List[Dict[str, Any]] = []
    for m in lista_materias:
        if not isinstance(m, dict):
            continue
        ementa = m.get("Ementa", "") or ""
        ementa_norm = _normalizar_acentos(ementa)
        autor_norm = _normalizar_acentos(m.get("Autor"))
        if termo in ementa_norm or termo in autor_norm:
            filtradas.append({
                "codigo": m.get("Codigo"),
                "sigla": m.get("Sigla"),
                "tipo": m.get("DescricaoIdentificacao"),
                "numero": m.get("Numero"),
                "ano": m.get("Ano"),
                "ementa": ementa,
                "autor": m.get("Autor"),
                "data": m.get("Data"),
                "url": m.get("UrlDetalheMateria"),
            })

    return {
        "termo_pesquisado": q,
        "total": len(filtradas),
        "resultados": filtradas,
    }