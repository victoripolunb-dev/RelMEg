"""
Conector do Diário Oficial da União (DOU) — motor universal (relmeg_core).

Status B5 (10/09/2026): a API JSON legada da Imprensa Nacional
(``https://in.gov.br/es/web/dou/-/api/json``) foi descontinuada (404). O DOU no
RelMeg é uma fonte de **busca**: consumimos o motor de busca SR do próprio
portal (``https://www.in.gov.br/consulta/-/buscar/dou``), que embute os
resultados no HTML de forma estável — sempre sob demanda (AGENTS.md), dentro da
requisição do operador.

Publicações do diário NÃO têm ficha legislativa estruturada (tramitação,
autoria) comparável às casas: ``obter_projeto``, ``obter_tramitacoes`` e
``obter_parlamentar`` levantam ``DOUSemFichaEstruturada`` (mapeada para 501 nas
rotas do hub), orientando o operador a usar a busca
(``GET /hub/busca/proposicoes?fonte=dou``). Nenhum browser headless é
necessário aqui (os dados vêm no HTML server-rendered); por isso
``usa_scrapling = False``.
"""
from __future__ import annotations

import asyncio
import html as _html
import json
import re
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

import httpx

from relmeg_core.connectors.base_connector import LegislativoConnector
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

URL_PORTAL_SR = "https://www.in.gov.br/consulta/-/buscar/dou"

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
_TOTAL_PAGINAS = re.compile(r"totalPages\s*:\s*(\d+)")

# Tamanho de página suportado pelo portal (delta) e teto de coleta por
# requisição do operador (evita milhões de requisições — AGENTS.md).
DELTA = 20
MAX_ITENS = 200


class DOUSemFichaEstruturada(RuntimeError):
    """Fonte de busca: publicações do DOU não têm ficha legislativa por item.

    Use ``buscar_proposicoes`` (resumos ricos, já com ementa/complemento) ou a
    rota legada ``GET /dou/pesquisa``. Mapeada para 501 nas rotas do hub.
    """


# ---------------------------------------------------------------------------
# Parsing do HTML do portal (server-rendered, sem browser headless)
# ---------------------------------------------------------------------------


def _reparar_mojibake(texto: str) -> str:
    """Corrige resquícios de dupla codificação latin-1→UTF-8 (nós do portal)."""
    if "Ã" not in texto and "Â" not in texto and "\ufffd" not in texto:
        return texto
    try:
        candidato = texto.encode("latin-1").decode("utf-8", errors="replace")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto
    return candidato if candidato.count("\ufffd") <= texto.count("\ufffd") else texto


def _decodificar_html(payload: Any) -> str:
    """Decodifica o HTML do portal como UTF-8, com fallback para nós despadronizados.

    O portal declara ``text/html;charset=UTF-8``, mas alguns nós já responderam
    com bytes latin-1 soltos. Preferimos UTF-8 estrito e, em falha, tentamos a
    interpretação latin-1 + reparo de mojibake.
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
                return json.loads(corpo)
            except Exception:  # noqa: BLE001 (nó parcial — tenta o próximo)
                continue
    return {}


def formatar_hit(hit: dict) -> dict:
    """Normaliza um resultado do portal para o formato do motor/hub."""
    url_title = hit.get("urlTitle")
    return {
        "fonte": "dou",
        "id_externo": url_title,
        "titulo": _limpar_html(hit.get("title")),
        "orgao": hit.get("hierarchyStr") or hit.get("pubName"),
        "data_publicacao": hit.get("pubDate"),
        "url": f"https://in.gov.br/web/dou/-/{url_title}" if url_title else None,
        "tipo": hit.get("artType"),
        "ementa": _limpar_html(hit.get("content")),
        "edicao": hit.get("editionNumber"),
        "pagina": hit.get("numberPage"),
    }


def parametros_busca(
    q: str,
    secao: int = 1,
    data: Optional[str] = None,
    ano: Optional[int] = None,
) -> dict:
    """Parâmetros da busca SR do portal (data AAAA-MM-DD ou ano de publicação)."""
    params: dict = {
        "q": q,
        "s": f"do{secao}",
        "sortType": "0",
        "delta": str(DELTA),
    }
    if data:
        params["exactDate"] = "personalizado"
        dia, mes, ano4 = data[8:10], data[5:7], data[0:4]
        params["publishFrom"] = f"{dia}-{mes}-{ano4}"
        params["publishTo"] = f"{dia}-{mes}-{ano4}"
    elif ano:
        params["exactDate"] = "personalizado"
        params["publishFrom"] = f"01-01-{ano}"
        params["publishTo"] = f"31-12-{ano}"
    else:
        params["exactDate"] = "mes"
    return params


def url_busca_oficial(
    q: str,
    secao: int = 1,
    data: Optional[str] = None,
    ano: Optional[int] = None,
) -> str:
    """Deep link oficial de busca no portal do DOU (Imprensa Nacional).

    O portal interpreta as datas no formato DD-MM-AAAA (o JS da própria SPA
    converte o valor do datepicker assim antes do submit).
    """
    params = parametros_busca(q, secao, data, ano)
    base = f"{URL_PORTAL_SR}?q={quote(q)}&s=todos&exactDate={params['exactDate']}&sortType=0"
    if params.get("publishFrom"):
        base += f"&publishFrom={quote(params['publishFrom'])}&publishTo={quote(params['publishTo'])}"
    elif params["exactDate"] == "mes":
        base += "&publish=past-month"
    base += f"&s=do{secao}"
    return base


def coletar_portal_sr(
    q: str,
    secao: int = 1,
    data: Optional[str] = None,
    ano: Optional[int] = None,
    itens: int = MAX_ITENS,
) -> List[dict]:
    """Varre a busca SR do portal (paginada) e devolve até ``itens`` resultados.

    100% sob demanda (chamado apenas dentro de uma requisição/acão do operador).
    Paginação via cursor serrilhado do portal (score/classPK/displayDateSortable),
    mesma semântica do ``doSearch``. Funciona sem browser: o portal entrega o
    JSON embutido no HTML server-rendered.
    """
    itens = max(1, min(int(itens), MAX_ITENS))
    coletados: List[dict] = []
    pagina_atual = 0
    total_paginas = 1
    cursor = {"score": "0", "id": "", "displayDate": ""}

    with httpx.Client(headers=_HEADERS, timeout=30.0, follow_redirects=True) as cliente:
        while len(coletados) < itens and pagina_atual < total_paginas:
            params = parametros_busca(q, secao, data, ano)
            if pagina_atual > 0:
                params.update({
                    "currentPage": str(pagina_atual),
                    "newPage": str(pagina_atual + 1),
                    "score": cursor["score"],
                    "id": cursor["id"],
                    "displayDate": cursor["displayDate"],
                })

            try:
                resposta = cliente.get(URL_PORTAL_SR, params=params)
            except httpx.HTTPError:
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
                if len(coletados) >= itens:
                    break
                coletados.append(formatar_hit(hit))

            ultimo = hits[-1]
            cursor = {
                "score": str(ultimo.get("score") or "0"),
                "id": str(ultimo.get("classPK") or ""),
                "displayDate": str(ultimo.get("displayDateSortable") or ""),
            }
            pagina_atual += 1

    return coletados


class DouConnector(LegislativoConnector):
    """Conector DOU — fonte de busca por palavras-chave (resumos ricos).

    O DOU não oferece ficha legislativa estruturada por item (sem tramitação/
    autoria). ``buscar_proposicoes`` devolve os resultados normalizados do
    portal (title, órgão, data, ementa/complemento, URL, tipo, edição, página);
    os métodos de coleta por item levantam ``DOUSemFichaEstruturada`` (501).
    """

    fonte = "dou"
    url_raiz = "https://www.in.gov.br"
    status_v1 = "pronta (busca)"
    usa_scrapling = False  # dados vêm no HTML server-rendered — sem headless

    async def obter_parlamentar(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ParlamentarModel:
        raise DOUSemFichaEstruturada(
            "DOU: não há parlamentares a coletar — fonte de busca de publicações. "
            "Use GET /hub/busca/proposicoes?fonte=dou."
        )

    async def obter_projeto(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ProjetoDeLeiModel:
        raise DOUSemFichaEstruturada(
            "DOU: publicações do diário não têm ficha legislativa estruturada. "
            "Use a busca (GET /hub/busca/proposicoes?fonte=dou) ou a rota "
            "legada GET /dou/pesquisa."
        )

    async def obter_tramitacoes(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> List[TramitacaoModel]:
        raise DOUSemFichaEstruturada(
            "DOU: não há tramitação — fonte de busca de publicações do diário."
        )

    async def buscar_proposicoes(
        self,
        termo: str,
        *,
        sigla_tipo: Optional[str] = None,
        ano: Optional[int] = None,
        itens: int = 25,
        **opcoes: Any,
    ) -> List[Dict[str, Any]]:
        """Busca publicações no DOU pelo motor de busca SR do portal (sob demanda).

        Aceita, via ``**opcoes``: ``secao`` (1..3, default 1) e ``data``
        (AAAA-MM-DD). ``ano`` filtra pela janela de publicação do ano.
        """
        secao = int(opcoes.get("secao", 1))
        if not 1 <= secao <= 3:
            raise ValueError("secao deve estar entre 1 e 3 (1=atos normativos, 2=pessoal, 3=contratos).")
        data = opcoes.get("data")
        if data:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(data)):
                raise ValueError("data deve estar no formato AAAA-MM-DD.")
        limite = max(1, min(int(itens), MAX_ITENS))
        return await asyncio.to_thread(
            coletar_portal_sr, termo, secao=secao, data=data, ano=ano, itens=limite
        )