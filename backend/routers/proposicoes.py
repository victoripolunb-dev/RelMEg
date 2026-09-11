from fastapi import APIRouter, HTTPException, Query
from starlette.requests import Request
from typing import Optional
import httpx
import asyncio
import re

from rate_limit import limiter, LIMITE_PROPOSICOES

router = APIRouter(prefix="/proposicoes", tags=["Proposições"])

URL_BASE = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"

_TEXTO_AUTOR = re.compile(r"^(Dep\.|Deputad[oa]|Parlamentar)\s*", re.IGNORECASE)


def _extrair_relator(despacho: Optional[str]) -> Optional[str]:
    """Tenta extrair o nome do relator a partir de um despacho de tramitação."""
    if not despacho:
        return None
    match = re.search(r"[Rr]elator[a]?\s*[:\-]?\s*(.+?)(?:[.,;]|$)", despacho)
    if not match:
        return None
    nome = _TEXTO_AUTOR.sub("", match.group(1)).strip()
    return nome if nome else None


async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    resposta = await client.get(url, headers={"Accept": "application/json"})
    resposta.raise_for_status()
    return resposta.json()


async def _enriquecer_camara(
    client: httpx.AsyncClient,
    proposicao: dict,
) -> dict:
    """Busca situação/comissão atual e relator de uma proposição da Câmara.

    A API retorna `descricaoSituacao` nula para proposições recém-apresentadas;
    nesse caso, complementa com o último registro de tramitação.
    """
    id_proposicao = proposicao.get("id")
    if not id_proposicao:
        return {"situacao": None, "comissao": None, "relator": None}

    try:
        detalhe, tramitacoes = await asyncio.gather(
            _get_json(client, f"{URL_BASE}/{id_proposicao}"),
            _get_json(client, f"{URL_BASE}/{id_proposicao}/tramitacoes"),
        )
    except Exception:
        return {"situacao": None, "comissao": None, "relator": None}

    conteudo = detalhe.get("dados") or detalhe
    status = conteudo.get("statusProposicao") or {}

    situacao = status.get("descricaoSituacao")
    orgao = status.get("orgaoAtual") or status.get("orgaoNumerador") or {}
    comissao = orgao.get("sigla")
    relator = _extrair_relator(status.get("despacho"))

    registros = tramitacoes.get("dados") or []
    if isinstance(registros, dict):
        registros = [registros]
    registros = [
        r for r in registros
        if isinstance(r, dict)
    ]
    # Garante a ordenação mais recente por último (a API costuma vir ordenada,
    # mas o contrato não é verificado — aqui normalizamos por data quando houver).
    registros.sort(
        key=lambda r: r.get("dataHora") or r.get("data") or r.get("situacao") or "",
    )

    if registros:
        ultimo = registros[-1]
        if not situacao:
            situacao = ultimo.get("situacao")
        if not comissao:
            destino = ultimo.get("destinoTramitacao") or {}
            comissao = destino.get("sigla")

        if not relator:
            for tramite in reversed(registros):
                nome = _extrair_relator(tramite.get("despacho"))
                if nome:
                    relator = nome
                    break

    return {
        "situacao": situacao,
        "comissao": comissao,
        "relator": relator,
    }


async def _listar_proposicoes(
    siglaTipo: Optional[str],
    ano: Optional[int],
    keywords: Optional[str],
    itens: int,
    enriquecer: bool,
) -> dict:
    """Busca proposições legislativas na API da Câmara, com situação, comissão e relator.

    Implementação interna compartilhada entre a rota HTTP decorada e a
    fachada (/api/camara) — sem rate limit próprio para não duplicar cotas.
    """

    params = {
        "itens": itens,
        "ordem": "desc",
        "ordenarPor": "id",
    }
    if siglaTipo:
        params["siglaTipo"] = siglaTipo
    if ano:
        params["ano"] = ano
    if keywords:
        params["keywords"] = keywords

    timeout = httpx.Timeout(30.0)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resposta = await client.get(URL_BASE, params=params, headers={"Accept": "application/json"})
            if resposta.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail="Não foi possível acessar a API da Câmara",
                )
            dados = resposta.json()
            proposicoes = dados.get("dados", [])

            proposicoes_formatadas = [
                {
                    "id": prop.get("id"),
                    "siglaTipo": prop.get("siglaTipo"),
                    "numero": prop.get("numero"),
                    "ano": prop.get("ano"),
                    "ementa": prop.get("ementa"),
                    "situacao": None,
                    "comissao": None,
                    "relator": None,
                }
                for prop in proposicoes
            ]

            if enriquecer and proposicoes_formatadas:
                riquezas = await asyncio.gather(
                    *[_enriquecer_camara(client, p) for p in proposicoes_formatadas]
                )
                for prop, riqueza in zip(proposicoes_formatadas, riquezas):
                    prop.update(riqueza)

            return {
                "total": len(proposicoes_formatadas),
                "filtros_aplicados": {
                    "siglaTipo": siglaTipo,
                    "ano": ano,
                    "keywords": keywords,
                },
                "proposicoes": proposicoes_formatadas,
            }
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Não foi possível acessar a API da Câmara") from exc


@router.get("/")
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_proposicoes(
    request: Request,
    siglaTipo: Optional[str] = Query(None, description="Ex: PL, PEC, MPV"),
    ano: Optional[int] = Query(None, ge=1900, le=2100, description="Ex: 2026, 2025"),
    keywords: Optional[str] = Query(None, min_length=3, max_length=120, description="Palavra-chave para buscar na ementa (ex: energia, imposto)"),
    itens: int = Query(10, ge=1, le=100, description="Quantidade máxima de itens retornados"),
    enriquecer: bool = Query(True, description="Se false, retorna apenas os campos básicos (mais rápido)"),
):
    """Busca proposições legislativas na API da Câmara, com situação, comissão e relator."""
    return await _listar_proposicoes(
        siglaTipo=siglaTipo,
        ano=ano,
        keywords=keywords,
        itens=itens,
        enriquecer=enriquecer,
    )