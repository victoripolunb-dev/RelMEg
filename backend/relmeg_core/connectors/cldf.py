"""
Conector da Câmara Legislativa do Distrito Federal (CLDF) — PLE.

A API pública do Processo Legislativo Eletrônico (PLE) não expõe o histórico de
tramitação: o endpoint por ``id`` e os sub-recursos ``/autores`` e
``/tramitacoes`` retornam 404/lista vazia; o item do ``filter`` só traz a etapa
ATUAL (retrato). O histórico completo vive no portal
(``/web/guest/acompanhar-andamento?idProposicao=<id PLE>``), com a tabela
renderizada por JS. Estratégia de tramitação (on-demand):

    1. Se o fallback Scrapling estiver ligado, raspar o andamento do portal;
    2. Falhou/vazio → entregar a etapa atual da API (comportamento original).

Referências:
    - https://dados.cl.df.gov.br/pt_PT/dataset/proposicoes
    - https://ple.cl.df.gov.br/pleservico/api/public
    - https://www.cl.df.gov.br/web/guest/acompanhar-andamento
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from relmeg_core.connectors.base_connector import (
    _data_br,
    _scrapling_instalado,
    extrair_linha_tempo,
    LegislativoConnector,
)
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

URL_BASE = "https://ple.cl.df.gov.br/pleservico/api/public"
# Página pública de andamento da matéria (portlet JS da CLDF). O ID é o mesmo
# "id" do PLE que a API de filter nos devolve — sem precisar do id do Sileg.
URL_ANDAMENTO = "https://www.cl.df.gov.br/web/guest/acompanhar-andamento"

# Sigla → rótulo aceito pelo filtro da CLDF (melhor esforço; cobre o comum).
_SIGLA_PARA_TIPO = {
    "PL": "Projeto de Lei",
    "PELO": "Proposta de Emenda à Lei Orgânica",
    "PR": "Projeto de Resolução",
    "PDL": "Projeto de Decreto Legislativo",
    "IND": "Indicação",
    "MOC": "Moção",
    "REQ": "Requerimento",
}

_RE_SIGLA_NUM_ANO = re.compile(r"^(?P<sigla>[A-ZÀ-Ú]{1,8})\s*(?P<numero>\d+)/(?P<ano>\d{4})$")


def _parse_identificacao(id_externo: str) -> Optional[Dict[str, str]]:
    """Quebra ``"PL 2473/2026"`` em sigla/numero/ano (ou None se inválido)."""
    m = _RE_SIGLA_NUM_ANO.match(str(id_externo).strip().upper())
    if not m:
        return None
    return m.groupdict()


class CldfConnector(LegislativoConnector):
    """Conector da Câmara Legislativa do DF (API pública do PLE)."""

    fonte = "cldf"
    url_raiz = URL_BASE

    # ------------------------------------------------------------------
    # Parlamentar (catálogo de autores da CLDF)
    # ------------------------------------------------------------------

    async def obter_parlamentar(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ParlamentarModel:
        catalogo = await self._http_get_json(f"{URL_BASE}/autor/listar")
        alvo = str(id_externo).strip()
        alvo_low = alvo.lower()
        encontrado: Optional[Dict[str, Any]] = None
        for autor in catalogo or []:
            if not isinstance(autor, dict):
                continue
            if str(autor.get("id")) == alvo or str(autor.get("nome") or "").strip().lower() == alvo_low:
                encontrado = autor
                break

        if not encontrado:
            raise ValueError(f"CLDF: autor não localizado no catálogo: {id_externo}")

        nome = str(encontrado.get("nome") or "Indisponível").strip()
        return ParlamentarModel(
            fonte=self.fonte,
            url_origem=f"{URL_BASE}/autor/listar",
            id_externo=str(encontrado.get("id") or id_externo),
            nome_completo=nome,
            uf="DF",
            nome_urna=nome,
            status_ativo=str(encontrado.get("situacao") or "").upper() == "ATIVO",
            cargo="Deputado Distrital",
        )

    # ------------------------------------------------------------------
    # Proposição
    # ------------------------------------------------------------------

    async def obter_projeto(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ProjetoDeLeiModel:
        item = await self._buscar_item(id_externo)
        if item is None:
            raise ValueError(
                f"CLDF: proposição não localizada: {id_externo} (use formato 'PL 2473/2026')"
            )
        return self._mapear_projeto(item, id_externo)

    async def _buscar_item(self, id_externo: str) -> Optional[Dict[str, Any]]:
        """Localiza a proposição no PLE via POST /filter (filtro sob demanda)."""
        parte = _parse_identificacao(id_externo)
        if not parte:
            return None
        payload: Dict[str, Any] = {
            "numeroProposicao": parte["numero"],
            "ano": parte["ano"],
        }
        tipo = _SIGLA_PARA_TIPO.get(parte["sigla"])
        if tipo:
            payload["tipoProposicao"] = tipo

        corpo = await self._http_post_json(
            f"{URL_BASE}/proposicao/filter",
            params={"page": 0, "size": 20, "sort": "dataLeitura,DESC"},
            json={chave: valor for chave, valor in payload.items() if valor},
        )
        if not isinstance(corpo, dict):
            logger.warning("cldf: resposta filter inesperada para {id} (não-dict)", id=id_externo)
            return None
        itens = corpo.get("content") or []
        if not isinstance(itens, list):
            return None

        esperado = str(id_externo).strip().upper().replace(" ", "")
        for item in itens:
            sigla = self._sigla_numero_ano(item)
            if sigla and sigla.replace(" ", "") == esperado:
                return item
        logger.info(
            "cldf: nenhum item corresponde a {id} na busca (de {n} resultados)",
            id=id_externo,
            n=len(itens),
        )
        return None

    @staticmethod
    def _sigla_numero_ano(item: Dict[str, Any]) -> Optional[str]:
        valor = item.get("siglaNumeroAno")
        if valor:
            return str(valor)
        descricao = item.get("descricaoProposicao") or ""
        m = re.match(r"^(\d+)/(\d{4})\s*-\s*", descricao.strip())
        sigla_tipo = item.get("tipoProposicao") or ""
        if m:
            sigla = next((s for s, t in _SIGLA_PARA_TIPO.items() if t.lower() in sigla_tipo.lower()), "")
            return f"{sigla} {m.group(1)}/{m.group(2)}".strip()
        return None

    def _mapear_projeto(self, item: Dict[str, Any], id_externo: str) -> ProjetoDeLeiModel:
        sigla_numero_ano = self._sigla_numero_ano(item) or id_externo.strip()
        parte = _parse_identificacao(sigla_numero_ano or id_externo)
        autoria = str(item.get("autoria") or "").strip()
        tema = str(item.get("temaNome") or "").strip().strip("#")
        regiao = str(item.get("regiaoAdministrativaNome") or "").strip().strip("#")
        pauta = ", ".join(parte for parte in (tema, regiao) if parte) or None

        return ProjetoDeLeiModel(
            fonte=self.fonte,
            url_origem=f"{URL_BASE}/proposicao/filter",
            id_externo=sigla_numero_ano,
            sigla_tipo=parte["sigla"] if parte else (item.get("tipoProposicao") or "PL"),
            numero=int(parte["numero"]) if parte else 0,
            ano=int(parte["ano"]) if parte else 0,
            ementa=str(item.get("ementa") or item.get("parecer") or "Ementa indisponível").strip(),
            orgao_origem="cldf",
            url_documento=None,
            autor_principal=autoria.split(",")[0].strip() if autoria else None,
            integrantes=[autoria] if autoria else [],
            pauta_tematica=pauta,
            situacao=str(item.get("etapa") or item.get("situacaoProposicao") or "").strip() or None,
            comissao_atual=None,
            relator=None,
            data_apresentacao=self._data(item.get("dataLeitura")),
            status_sn=None,
        )

    @staticmethod
    def _data(valor: Any) -> Optional[date]:
        if not valor:
            return None
        try:
            return date.fromisoformat(str(valor)[:10])
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Tramitação (API da CLDF só dá a etapa atual; histórico extraído do portal)
    # ------------------------------------------------------------------

    async def obter_tramitacoes(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> List[TramitacaoModel]:
        item = await self._buscar_item(id_externo)
        if item is None:
            raise ValueError(f"CLDF: proposição não localizada: {id_externo}")

        sigla = self._sigla_numero_ano(item) or id_externo.strip()
        historico = await self._historico_via_portal(sigla, item)
        if historico:
            return historico

        etapa = str(item.get("etapa") or "").strip()
        if not etapa:
            logger.info("cldf: matéria {id} sem etapa informada (histórico indisponível)", id=id_externo)
            return []

        return [
            TramitacaoModel(
                fonte=self.fonte,
                url_origem=f"{URL_BASE}/proposicao/filter",
                id_proposicao_externo=sigla,
                data_evento=self._data(item.get("dataLeitura")),
                orgao_local=None,
                descricao_fase=etapa,
                status=etapa,
                sequencia=None,
                despacho=str(item.get("parecer") or "").strip() or None,
                url_evento=None,
            )
        ]

    async def _historico_via_portal(self, sigla: str, item: Dict[str, Any]) -> List[TramitacaoModel]:
        """Raspa o andamento completo da matéria no portal (fallback Scrapling).

        A tabela é renderizada por JS no browser headless; extraímos o texto do
        corpo e quebramos a linha do tempo por datas (dd/mm/aaaa). Devolve [] sem
        quebrar quando o fallback está desligado, a página falha ou o markup muda.
        """
        ple_id = item.get("id")
        if not self.usa_scrapling or not _scrapling_instalado() or not ple_id:
            return []

        extraido = await self._raspar(
            f"{URL_ANDAMENTO}?idProposicao={ple_id}", {"texto": "body"}
        )
        passos = extrair_linha_tempo(
            (extraido or {}).get("texto") or "", marcador="Detalhamento do andamento"
        )
        if not passos:
            logger.info(
                "cldf: portal não expôs andamento de {sigla}; mantendo etapa da API",
                sigla=sigla,
            )
            return []

        logger.info(
            "cldf: histórico do portal de {sigla}: {n} passos",
            sigla=sigla,
            n=len(passos),
        )
        return [
            TramitacaoModel(
                fonte=self.fonte,
                url_origem=f"{URL_ANDAMENTO}?idProposicao={ple_id}",
                id_proposicao_externo=sigla,
                data_evento=_data_br(data_txt),
                orgao_local=None,
                descricao_fase=descricao,
                status=descricao,
                sequencia=sequencia,
                despacho=None,
                url_evento=None,
            )
            for sequencia, (data_txt, descricao) in enumerate(passos, start=1)
        ]