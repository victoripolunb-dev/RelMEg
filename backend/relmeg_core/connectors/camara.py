"""
Conector da Câmara dos Deputados — caminho feliz (API Dados Abertos v2).

Mapeia o payload JSON/XML oficial para os modelos universais do RelMeg.
Nenhuma raspagem aqui: se a API falhar (403/503/timeout), o orquestrador é
quem decide usar o fallback (``relmeg_core/utils/scrapling_engine.py``).

Referências oficiais:
    - https://www2.camara.leg.br/swagger-ui/index.html
    - GET /api/v2/deputados/{id}
    - GET /api/v2/proposicoes/{id}
    - GET /api/v2/proposicoes/{id}/autores
    - GET /api/v2/proposicoes/{id}/tramitacoes
"""
from __future__ import annotations

import asyncio
import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

import httpx
from loguru import logger

from relmeg_core.connectors.base_connector import (
    _data_br,
    LegislativoConnector,
)
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel
from relmeg_core.utils.helpers import para_int, parse_date_iso

URL_BASE = "https://dadosabertos.camara.leg.br/api/v2"
# Ficha de tramitação do portal (server-rendered) — usada apenas quando a API
# oficial falha ou devolve vazio (fallback Scrapling, sempre sob demanda).
URL_FICHA_TRAMITACAO = "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao"

_RE_RELATOR_POR = re.compile(r"pelo\s+(?:Dep\.|Deputad[oa])\s+([A-ZÀ-Ú][^,;.(]*?)(?:\s*\(|[,;.]|$)")
_RE_RELATOR_TITULO = re.compile(
    r"[Rr]elator[a]?\s*(?:[,:\-])\s*(?:Dep\.|Deputad[oa]|Parlamentar)\s+([A-ZÀ-Ú][^,;.]*?)(?:\s*\(|[,;.]|$)"
)
_RE_TITULO_AUTOR = re.compile(r"^(Dep\.|Deputad[oa]|Parlamentar)\s*", re.IGNORECASE)


def _sem_erro(valor: Any) -> Optional[Dict[str, Any]]:
    """Converte exceção do gather em None (enriquecimento best-effort)."""
    if isinstance(valor, BaseException):
        return None
    return valor

# Compat: helpers compartilhados (módulo relmeg_core.utils.helpers).
_para_int = para_int
_data_iso = parse_date_iso

# Condições eleitorais que indicam parlamentar FORA de exercício.
_SITUACOES_INATIVAS = {
    "renúncia", "renuncia", "renunciou", "cassado", "cassação", "vago",
    "falecido", "licenciado", "aposentado",
}


def _extrair_relator(despacho: Optional[str]) -> Optional[str]:
    """Extrai o nome do relator de um despacho (heurística com padrões-alvo).

    Prioriza "pelo Deputado <Nome>" e "Relator{,:-} Dep. <Nome>"; descarta
    capturas inválidas (número de parecer, "Parecer do Relator" sem nome...).
    """
    if not despacho:
        return None

    for padrao in (_RE_RELATOR_POR, _RE_RELATOR_TITULO):
        m = padrao.search(despacho)
        if m:
            nome = _RE_TITULO_AUTOR.sub("", m.group(1)).strip()
            if nome:
                return nome
    return None


def _array_palavras(valor: Any) -> Optional[str]:
    """Converte keywords (lista ou string) em texto único separado por vírgula."""
    if valor is None:
        return None
    if isinstance(valor, list):
        return ", ".join(str(v).strip() for v in valor if str(v).strip())
    texto = str(valor).strip()
    return texto or None


def _autores_validos(dados: Any) -> List[str]:
    """Autores não-nulos da resposta de /autores (item pode vir com autor None)."""
    nomes: List[str] = []
    for item in dados or []:
        if not isinstance(item, dict):
            continue
        nome = item.get("autor")
        if nome and str(nome).strip():
            nomes.append(str(nome).strip())
    return nomes


class CamaraConnector(LegislativoConnector):
    """Conector da Câmara dos Deputados (API oficial v2)."""

    fonte = "camara"
    url_raiz = URL_BASE

    # ------------------------------------------------------------------
    # Parlamentar
    # ------------------------------------------------------------------

    async def obter_parlamentar(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ParlamentarModel:
        dados = await self._http_get_json(f"{URL_BASE}/deputados/{id_externo}")
        registro = dados.get("dados") or {}
        status = registro.get("ultimoStatus") or {}

        modelo = ParlamentarModel(
            fonte=self.fonte,
            url_origem=f"{URL_BASE}/deputados/{id_externo}",
            id_externo=str(registro.get("id") or id_externo),
            nome_completo=registro.get("nomeCivil") or "Indisponível",
            partido=status.get("siglaPartido"),
            uf=status.get("siglaUf") or "BR",
            nome_urna=status.get("nomeEleitoral") or status.get("nome"),
            status_ativo=self._ativa(status.get("condicaoEleitoral")),
            cargo="Deputado Federal",
            email=status.get("email"),
            url_foto=status.get("urlFoto"),
            url_perfil=registro.get("uri"),
        )
        return modelo

    @staticmethod
    def _ativa(condicao: Optional[str]) -> bool:
        if not condicao:
            return True
        return condicao.strip().lower() not in _SITUACOES_INATIVAS

    # ------------------------------------------------------------------
    # Proposição
    # ------------------------------------------------------------------

    async def obter_projeto(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        complementar_situacao: bool = True,
        **opcoes: Any,
    ) -> ProjetoDeLeiModel:
        detalhe, autores = await asyncio.gather(
            self._http_get_json(f"{URL_BASE}/proposicoes/{id_externo}"),
            self._http_get_json(f"{URL_BASE}/proposicoes/{id_externo}/autores"),
            return_exceptions=True,
        )
        if isinstance(detalhe, BaseException):
            raise detalhe
        autores = _sem_erro(autores)
        registro = detalhe.get("dados") or detalhe
        status = registro.get("statusProposicao") or {}

        situacao = status.get("descricaoSituacao")
        comissao = status.get("siglaOrgao")
        relator = _extrair_relator(status.get("despacho"))

        # Complemento de informação: matéria recém-apresentada pode vir com
        # situação nula; então lê o último registro de tramitação sob demanda.
        # Busca seletiva (contrato do operador): se o pedido restringe campos e
        # o enriquecimento não é um deles, pulamos a chamada extra.
        campos_nao_pedem_enriquecimento = campos is not None and not any(
            c in {"situacao", "comissao_atual", "relator"} for c in campos
        )
        if complementar_situacao and not situacao and not campos_nao_pedem_enriquecimento:
            try:
                tramitacoes = await self.obter_tramitacoes(id_externo)
                if tramitacoes:
                    ultima = tramitacoes[-1]
                    situacao = situacao or ultima.descricao_fase
                    comissao = comissao or ultima.orgao_local
                    relator = relator or _extrair_relator(ultima.despacho)
            except Exception as exc:  # noqa: BLE001
                logger.debug("camara: falha ao complementar situação de {id}: {e}", id=id_externo, e=exc)

        autores_nomes = _autores_validos(autores.get("dados"))

        modelo = ProjetoDeLeiModel(
            fonte=self.fonte,
            url_origem=f"{URL_BASE}/proposicoes/{id_externo}",
            id_externo=str(registro.get("id") or id_externo),
            sigla_tipo=registro.get("siglaTipo") or "PL",
            numero=_para_int(registro.get("numero")),
            ano=_para_int(registro.get("ano")),
            ementa=registro.get("ementa") or "Ementa indisponível",
            orgao_origem="camara",
            url_documento=registro.get("urlInteiroTeor") or registro.get("uri"),
            autor_principal=autores_nomes[0] if autores_nomes else None,
            integrantes=autores_nomes,
            pauta_tematica=_array_palavras(registro.get("keywords")),
            situacao=situacao,
            comissao_atual=comissao,
            relator=relator,
            data_apresentacao=_data_iso(registro.get("dataApresentacao")),
            status_sn=str(status.get("codSituacao")) if status.get("codSituacao") is not None else None,
        )
        return modelo

    # ------------------------------------------------------------------
    # Busca por palavras-chave (on-demand, resumos normalizados)
    # ------------------------------------------------------------------

    async def buscar_proposicoes(
        self,
        termo: str,
        *,
        sigla_tipo: Optional[str] = None,
        ano: Optional[int] = None,
        itens: int = 25,
        **opcoes: Any,
    ) -> List[Dict[str, Any]]:
        """Busca proposições por keywords na API da Câmara (GET /proposicoes?keywords=).

        Retorna RESUMOS normalizados da listagem — nada é persistido aqui. Para o
        payload completo + persistência, o operador dispara
        POST /hub/proposicoes/camara/{id_externo} por resultado de interesse.
        Filtros ``sigla_tipo``/``ano`` são opcionais (janela de varredura do
        operador, sempre sob demanda — AGENTS.md).
        """
        params: Dict[str, Any] = {
            "keywords": termo,
            "itens": itens,
            "ordem": "desc",
            "ordenarPor": "id",
        }
        if sigla_tipo:
            params["siglaTipo"] = sigla_tipo
        if ano:
            params["ano"] = ano

        resposta = await self._http_get_json(f"{URL_BASE}/proposicoes", params=params)
        itens_lista = resposta.get("dados") or []
        if isinstance(itens_lista, dict):
            itens_lista = [itens_lista]

        resultados: List[Dict[str, Any]] = []
        for item in itens_lista:
            if not isinstance(item, dict):
                continue
            resultados.append(
                {
                    "fonte": self.fonte,
                    "url_origem": item.get("uri")
                    or f"{URL_BASE}/proposicoes/{item.get('id')}",
                    "id_externo": str(item.get("id") or ""),
                    "sigla_tipo": item.get("siglaTipo") or "PL",
                    "numero": _para_int(item.get("numero")),
                    "ano": _para_int(item.get("ano")),
                    "ementa": item.get("ementa") or "Ementa indisponível",
                    "data_apresentacao": str(_data_iso(item.get("dataApresentacao")))
                    if item.get("dataApresentacao") else None,
                    "url_documento": item.get("urlInteiroTeor") or item.get("uri"),
                }
            )
        return resultados

    # ------------------------------------------------------------------
    # Tramitação
    # ------------------------------------------------------------------

    async def obter_tramitacoes(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> List[TramitacaoModel]:
        try:
            resposta = await self._http_get_json(f"{URL_BASE}/proposicoes/{id_externo}/tramitacoes")
            itens = resposta.get("dados") or []
            if isinstance(itens, dict):
                itens = [itens]
            tramitacoes = [self._mapear_tramitacao(item, id_externo) for item in itens]
        except httpx.HTTPError:
            logger.warning(
                "camara: API de tramitações indisponível para {id}; tentando a ficha web",
                id=id_externo,
            )
            tramitacoes = []

        if tramitacoes:
            return tramitacoes

        logger.info("camara: sem tramitações na API para {id}; tentando a ficha web", id=id_externo)
        return await self._tramitacoes_via_ficha(id_externo)

    @staticmethod
    def _mapear_tramitacao(item: Dict[str, Any], id_externo: str) -> TramitacaoModel:
        return TramitacaoModel(
            fonte="camara",
            url_origem=str(item.get("url")) or f"{URL_BASE}/proposicoes/{id_externo}/tramitacoes",
            id_proposicao_externo=str(id_externo),
            data_evento=_data_iso(item.get("dataHora")),
            orgao_local=item.get("siglaOrgao"),
            descricao_fase=item.get("descricaoTramitacao"),
            status=item.get("descricaoSituacao") or item.get("descricaoTramitacao") or "Indisponível",
            sequencia=_para_int(item.get("sequencia"), default=None),
            despacho=item.get("despacho"),
            url_evento=item.get("url"),
        )

    async def _tramitacoes_via_ficha(self, id_externo: str) -> List[TramitacaoModel]:
        """Fallback: raspa a ficha de tramitação do portal (HTML estável).

        Estrutura validada em 10/09/2026: div ``#tramitacoes`` com tabela
        ``table.bordered``; cada linha tem 1ª célula = data (dd/mm/aaaa) e 2ª
        célula = órgão (``p.paragrafoTabelaTramitacoes``) + descrição
        (``ul.ulTabelaTramitacoes``). Devolve [] (nunca quebra) se o markup
        mudar ou a página falhar.
        """
        seletores = {
            "datas": {"css": "#tramitacoes tbody tr td:first-child", "lista": True},
            "orgaos": {"css": "#tramitacoes tbody tr p.paragrafoTabelaTramitacoes", "lista": True},
            "descricoes": {"css": "#tramitacoes tbody tr ul.ulTabelaTramitacoes", "lista": True},
        }
        extraido = await self._raspar(f"{URL_FICHA_TRAMITACAO}?idProposicao={id_externo}", seletores)
        if not extraido:
            return []

        datas = extraido.get("datas") or []
        orgaos = extraido.get("orgaos") or []
        descricoes = extraido.get("descricoes") or []
        n = min(len(datas), len(orgaos), len(descricoes))
        if n == 0:
            return []

        eventos: List[TramitacaoModel] = []
        for i in range(n):
            descricao = self._limpar_descricao(descricoes[i])
            eventos.append(
                TramitacaoModel(
                    fonte=self.fonte,
                    url_origem=f"{URL_FICHA_TRAMITACAO}?idProposicao={id_externo}",
                    id_proposicao_externo=str(id_externo),
                    data_evento=_data_br(datas[i]),
                    orgao_local=str(orgaos[i]).strip() or None,
                    descricao_fase=descricao,
                    status=descricao or "Indisponível",
                    sequencia=i + 1,
                    despacho=None,
                    url_evento=None,
                )
            )
        eventos.sort(key=lambda e: e.data_evento or date.min)
        return eventos

    @staticmethod
    def _limpar_descricao(valor: Any) -> Optional[str]:
        """Normaliza a descrição raspada da ficha (remove sufixos de UI)."""
        texto = str(valor or "").strip()
        texto = re.sub(r"\s*Inteiro teor\s*$", "", texto, flags=re.IGNORECASE)
        return texto.strip() or None