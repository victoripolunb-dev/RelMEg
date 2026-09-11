from fastapi import APIRouter, HTTPException, Query
from starlette.requests import Request
from typing import Optional
import httpx
import asyncio
import re
from loguru import logger

from rate_limit import limiter, LIMITE_PROPOSICOES

router = APIRouter(prefix="/senado/materias", tags=["Senado - Matérias"])

URL_PESQUISA = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"
URL_PROCESSO = "https://legis.senado.leg.br/dadosabertos/processo"

_HEADERS = {"Accept": "application/json"}


def _primeiro_token_identificacao(identificacao: Optional[str]) -> Optional[str]:
    """Extrai a sigla do tipo a partir do campo IdentificacaoProcesso (ex: 'PL 1/2024')."""
    if not identificacao:
        return None
    token = identificacao.strip().split(" ", 1)[0]
    return token or None


_PADRAO_RELATOR = re.compile(r"[Rr]elator(?:a|\(a\))?\s*[:\-]?\s*([A-ZÀ-Ú][^,;\n]{3,80})")
_PADRAO_DISTRIBUICAO = re.compile(
    r"Distribu(?:ído|ido|da)\s+(?:à|ao|a|o|para)\s+([^,;\n]{3,80}),?\s+para\s+emitir\s+r(?:e|é)lat[oó]ri",
    re.IGNORECASE,
)


async def _enriquecer_senado(
    client: httpx.AsyncClient,
    materia: dict,
) -> dict:
    """Busca situação/comissão atual e relator de uma matéria do Senado.

    Usa o serviço 'dadosabertos/processo' (substituto do antigo tramitacao,
    já descontinuado) para obter o andamento vigente. O comissão atual vem do
    colegiado de controle das autuações (CRA, CE etc.) e o relator, das
    descrições de situações, informes legislativos e movimentações.
    """
    id_processo = materia.get("identificacaoProcesso")
    if not id_processo:
        return {"situacao": None, "comissao": None, "relator": None}

    try:
        resposta = await client.get(f"{URL_PROCESSO}/{id_processo}", headers=_HEADERS)
        if resposta.status_code != 200:
            return {"situacao": None, "comissao": None, "relator": None}

        dados = resposta.json()
        situacao = dados.get("situacaoAtual")

        registros = (dados.get("despachos") or []) + (dados.get("autuacoes") or [])
        comissao = _comissao_atual(registros)
        relator = _relator_da_materia(registros)

        return {
            "situacao": situacao,
            "comissao": comissao,
            "relator": relator,
        }
    except Exception as exc:
        logger.debug(
            "senado: enriquecimento falhou para matéria {id}: {tipo}: {e}",
            id=id_processo, tipo=type(exc).__name__, e=exc,
        )
        return {"situacao": None, "comissao": None, "relator": None}


def _colegiado_em(registro) -> dict:
    """Normaliza o colegiado vigente de um despacho/autuação do Senado."""
    if not isinstance(registro, dict):
        return {}
    colegiado = (registro.get("encontroLegislativo") or {}).get("colegiado")
    if not colegiado:
        colegiado = registro.get("colegiado")
    if not colegiado:
        sigla = registro.get("siglaColegiadoControleAtual")
        nome = registro.get("nomeColegiadoControleAtual")
        if sigla or nome:
            colegiado = {"sigla": sigla, "nome": nome}
    return colegiado or {}


def _comissao_atual(registros) -> Optional[str]:
    """Último colegiado diferente do Plenário em despachos/autuações.

    A autuação expõe o colegiado de controle atual (ex.: CRA, CE) no próprio
    registro e dentro das suas situações; o despacho traz o colegiado no
    encontro legislativo. Nenhum deles deve ser confundido com o Plenário.
    """
    for registro in reversed(registros or []):
        if not isinstance(registro, dict):
            continue
        colegiados = []
        colegiado = _colegiado_em(registro)
        if colegiado:
            colegiados.append(colegiado)
        for situacao in reversed(registro.get("situacoes") or []):
            colegiado = _colegiado_em(situacao)
            if colegiado:
                colegiados.append(colegiado)
        for colegiado in colegiados:
            sigla = str(colegiado.get("sigla") or "").strip()
            nome = str(colegiado.get("nome") or "").strip()
            if sigla and sigla.upper() != "PLEN":
                return sigla
            if nome and "Plenário" not in nome:
                return nome
    return None


def _relator_da_materia(registros) -> Optional[str]:
    """Procura a designação de relator nas descrições do processo."""
    textos: list[str] = []
    for registro in registros or []:
        if not isinstance(registro, dict):
            continue
        for campo in ("tipoMotivacao", "descricao"):
            valor = registro.get(campo)
            if isinstance(valor, str):
                textos.append(valor)
        for situacao in registro.get("situacoes") or []:
            if isinstance(situacao, dict) and isinstance(situacao.get("descricao"), str):
                textos.append(situacao["descricao"])
        for informe in registro.get("informesLegislativos") or []:
            if isinstance(informe, dict) and isinstance(informe.get("descricao"), str):
                textos.append(informe["descricao"])
        for mov in registro.get("movimentacoes") or []:
            if isinstance(mov, dict) and isinstance(mov.get("descricao"), str):
                textos.append(mov["descricao"])

    for texto in reversed(textos):
        if not str(texto).strip():
            continue

        achado = _PADRAO_DISTRIBUICAO.search(texto)
        if achado:
            return achado.group(1).strip(" .")

        achado = _PADRAO_RELATOR.search(texto)
        if achado:
            nome = achado.group(1).strip(" .")
            if nome.lower() not in ("a", "o", "designado", "designação"):
                return nome
    return None


async def _listar_materias_senado(
    sigla: Optional[str],
    ano: Optional[int],
    tramitando: Optional[str],
    enriquecer: bool,
    keywords: Optional[str],
) -> dict:
    """Busca matérias legislativas no Senado Federal, com situação, comissão e relator.

    Implementação interna compartilhada entre a rota HTTP decorada e a
    fachada (/api/senado) — sem rate limit próprio para não duplicar cotas.
    """

    params = {}
    if sigla:
        params["sigla"] = sigla
    if ano:
        params["ano"] = ano
    if tramitando:
        params["tramitando"] = tramitando

    timeout = httpx.Timeout(30.0)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resposta = await client.get(URL_PESQUISA, params=params, headers=_HEADERS)
            if resposta.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail="Não foi possível acessar a API do Senado",
                )

            dados = resposta.json()
            pesquisa = dados.get("PesquisaBasicaMateria", {})
            lista_materias = pesquisa.get("Materias", {}).get("Materia", [])

            # Se vier apenas um item, o Senado pode retornar um dicionário em vez de lista
            if isinstance(lista_materias, dict):
                lista_materias = [lista_materias]

            materias_formatadas = [
                {
                    "codigo": m.get("Codigo"),
                    "sigla": m.get("Sigla"),
                    "tipo": _primeiro_token_identificacao(m.get("DescricaoIdentificacao")) or m.get("Sigla"),
                    "numero": m.get("Numero"),
                    "ano": m.get("Ano"),
                    "ementa": m.get("Ementa"),
                    "autor": m.get("Autor"),
                    "data": m.get("Data"),
                    "url": m.get("UrlDetalheMateria"),
                    "identificacaoProcesso": m.get("IdentificacaoProcesso"),
                    "situacao": None,
                    "comissao": None,
                    "relator": None,
                }
                for m in lista_materias
            ]

            if keywords:
                termo = keywords.strip().lower()
                if termo:
                    materias_formatadas = [
                        m for m in materias_formatadas
                        if termo in (m.get("ementa") or "").lower()
                        or termo in (m.get("autor") or "").lower()
                        or termo in (m.get("sigla") or "").lower()
                    ]

            if enriquecer and materias_formatadas:
                riquezas = await asyncio.gather(
                    *[_enriquecer_senado(client, m) for m in materias_formatadas]
                )
                for materia, riqueza in zip(materias_formatadas, riquezas):
                    materia.update(riqueza)

            return {
                "total": len(materias_formatadas),
                "materias": materias_formatadas,
            }
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Não foi possível acessar a API do Senado",
        ) from exc


@router.get("/")
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_materias_senado(
    request: Request,
    sigla: Optional[str] = Query(None, max_length=10, description="Sigla do tipo de matéria, ex: PEC, PL, PRS"),
    ano: Optional[int] = Query(None, ge=1900, le=2100, description="Ano da matéria, ex: 2026"),
    tramitando: Optional[str] = Query("S", pattern="^[SN]$", description="Apenas matérias em tramitação: S ou N"),
    enriquecer: bool = Query(True, description="Se false, retorna apenas os campos básicos (mais rápido)"),
    keywords: Optional[str] = Query(None, min_length=3, max_length=120, description="Termo livre para filtrar por ementa ou autor"),
):
    """Busca matérias legislativas no Senado Federal, com situação, comissão e relator."""
    return await _listar_materias_senado(
        sigla=sigla,
        ano=ano,
        tramitando=tramitando,
        enriquecer=enriquecer,
        keywords=keywords,
    )