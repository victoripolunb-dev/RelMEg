from fastapi import APIRouter, Query
from starlette.requests import Request
from typing import Any, List, Optional
import requests
import re
import html as _html
from urllib.parse import quote

from rate_limit import limiter, LIMITE_DOU

router = APIRouter(prefix="/dou", tags=["Diário Oficial da União (DOU)"])

# Erro conhecido de integração (2026): a API JSON legada
# (https://in.gov.br/es/web/dou/-/api/json) foi descontinuada pela Imprensa
# Nacional (retorna 404). O endpoint /dou/pesquisa agora consome o motor de
# busca do próprio portal (Liferay SR — busca ato-a-ato), que embute os
# resultados no HTML de forma estável e sob demanda.
_URL_PORTAL_SR = "https://www.in.gov.br/consulta/-/buscar/dou"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html",
}

_SCRIPT_PARAMS = re.compile(
    r'<script\b[^>]*_br_com_seatecnologia_in_buscadou_BuscaDouPortlet_params[^>]*>(.*?)</script>',
    re.S,
)
_TOTAL_RESULTADOS = re.compile(r"([\d.]+)\s+resultados para")
_TOTAL_PAGINAS = re.compile(r"totalPages\s*:\s*(\d+)")

# Tamanho de página suportado pelo portal (delta) e teto de coleta por
# requisição do operador (evita milhões de requisições ao portal — AGENTS.md:
# varredura on-demand, nunca rotina em segundo plano).
_DELTA = 20
_ITENS_MAX = 200


def _url_busca_oficial(q: str, secao: int, data: Optional[str]) -> str:
    """Deep link oficial de busca no portal do DOU (Imprensa Nacional).

    O portal interpreta as datas no formato DD-MM-AAAA (o JS da própria SPA
    converte o valor do datepicker assim antes do submit).
    """
    termo = quote(q)
    base = f"{_URL_PORTAL_SR}?q={termo}&s=todos&exactDate=personalizado&sortType=0"
    if data:
        dia, mes, ano = data[8:10], data[5:7], data[0:4]
        base += f"&publishFrom={quote(f'{dia}-{mes}-{ano}')}&publishTo={quote(f'{dia}-{mes}-{ano}')}"
    else:
        base += "&publish=past-month"
    base += f"&s=do{secao}"
    return base


def _reparar_mojibake(texto: str) -> str:
    """Corrige resquícios de dupla codificação latin-1→UTF-8 (nós do portal)."""
    if "Ã" not in texto and "Â" not in texto and "\ufffd" not in texto:
        return texto
    try:
        candidato = texto.encode("latin-1").decode("utf-8", errors="replace")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto
    return candidato if candidato.count("\ufffd") <= texto.count("\ufffd") else texto


def _decodificar_html(payload) -> str:
    """Decodifica o HTML do portal como UTF-8, com fallback para nós despadronizados.

    O portal declara ``text/html;charset=UTF-8``, mas alguns nós já responderam
    com bytes latin-1 soltos. Preferimos UTF-8 estrito (bytes reais do diário) e,
    em falha, tentamos a interpretação latin-1 + reparo de mojibake.
    """
    if isinstance(payload, str):
        return payload
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        texto = payload.decode("latin-1", errors="replace")
        candidato = texto.encode("latin-1").decode("utf-8", errors="replace")
        return candidato if candidato.count("\ufffd") < texto.count("\ufffd") else texto


def _limpar_html(texto: Any) -> str:
    """Remove destaque de busca (<span class='highlight'>) e tags HTML."""
    texto = str(texto or "").strip()
    texto = texto.replace("\\/", "/")
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = _html.unescape(texto).strip()
    return _reparar_mojibake(texto)


def _extrair_objeto_params(html_pagina: str) -> dict:
    """Extrai o objeto JSON embutido no script '_params' da página do portal.

    A ordem dos atributos do <script> varia entre respostas/nós (id antes ou
    depois de type), por isso o match é tolerante à posição.
    """
    for match in _SCRIPT_PARAMS.finditer(html_pagina):
        corpo = (match.group(1) or "").strip()
        if corpo.startswith("{"):
            try:
                import json
                return json.loads(corpo)
            except Exception:
                continue
    return {}


def _formatar_hit(hit: dict) -> dict:
    """Normaliza um resultado do portal para o formato histórico do endpoint."""
    url_title = hit.get("urlTitle")
    return {
        "titulo": _limpar_html(hit.get("title")),
        "orgao": hit.get("hierarchyStr") or hit.get("pubName"),
        "data_publicacao": hit.get("pubDate"),
        "url": f"https://in.gov.br/web/dou/-/{url_title}" if url_title else None,
        "tipo": hit.get("artType"),
        "ementa": _limpar_html(hit.get("content")),
        "edicao": hit.get("editionNumber"),
        "pagina": hit.get("numberPage"),
    }


def _coletar_portal(q: str, secao: int, data: Optional[str], limite: int) -> List[dict]:
    """Varre a busca SR do portal (paginada) e devolve até ``limite`` resultados.

    100% sob demanda (chamado apenas dentro da rota HTTP acionada pelo
    operador). A paginação usa o cursor serrilhado do portal
    (score/classPK/displayDateSortable), mesma semântica do ``doSearch``.
    """
    params: dict = {
        "q": q,
        "s": f"do{secao}",
        "sortType": "0",
        "delta": str(_DELTA),
    }
    if data:
        params["exactDate"] = "personalizado"
        dia, mes, ano = data[8:10], data[5:7], data[0:4]
        params["publishFrom"] = f"{dia}-{mes}-{ano}"
        params["publishTo"] = f"{dia}-{mes}-{ano}"
    else:
        # Sem data: mesmo comportamento do antigo deep link ("último mês").
        params["exactDate"] = "mes"

    coletados: List[dict] = []
    pagina_atual = 0
    total_paginas = 1
    cursor = {"score": "0", "id": "", "displayDate": ""}

    while len(coletados) < limite and pagina_atual < total_paginas:
        prm = dict(params)
        if pagina_atual > 0:
            prm.update({
                "currentPage": str(pagina_atual),
                "newPage": str(pagina_atual + 1),
                "score": cursor["score"],
                "id": cursor["id"],
                "displayDate": cursor["displayDate"],
            })

        try:
            resposta = requests.get(_URL_PORTAL_SR, params=prm, headers=_HEADERS, timeout=30.0)
        except requests.RequestException:
            break
        if resposta.status_code != 200:
            break

        html_decodificado = _decodificar_html(resposta.content)
        hits = _extrair_objeto_params(html_decodificado).get("jsonArray") or []
        if not hits:
            break

        if pagina_atual == 0:
            m = _TOTAL_PAGINAS.search(html_decodificado)
            if m:
                total_paginas = int(m.group(1))

        for hit in hits:
            if len(coletados) >= limite:
                break
            coletados.append(_formatar_hit(hit))

        ultimo = hits[-1]
        cursor = {
            "score": str(ultimo.get("score") or "0"),
            "id": str(ultimo.get("classPK") or ""),
            "displayDate": str(ultimo.get("displayDateSortable") or ""),
        }
        pagina_atual += 1

    return coletados


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
    ato-a-ato (https://www.in.gov.br/consulta/-/buscar/dou), de forma
    on-demand (AGENTS.md). Suporta paginação automática via cursor.
    """
    erros_base = {
        "erro": "Não foi possível recarregar os resultados do DOU deste período.",
        "url_busca_oficial": _url_busca_oficial(q, secao, data),
    }

    try:
        resultados = _coletar_portal(q, secao, data, limite=itens)
    except Exception:
        return erros_base

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
        "resultados": resultados,
        "url_busca_oficial": _url_busca_oficial(q, secao, data),
    }