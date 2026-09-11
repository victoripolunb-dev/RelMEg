"""
Conector do Senado Federal — caminho feliz (API Dados Abertos).

Mapeia o payload JSON/XML oficial para os modelos universais do RelMeg.
Chaves do Senado diferem das da Câmara (AnoMateria, SiglaSubtipoMateria,
NumeroMateria...); este conector normaliza isso. Se a API falhar, o
orquestrador decide o fallback (``relmeg_core/utils/scrapling_engine.py``).

Endpoints usados (Dados Abertos v2, on-demand):
    - GET /senador/{codigo}
    - GET /materia/{codigo}
    - GET /materia/autoria/{codigo}
    - GET /materia/movimentacoes/{codigo}   (histórico; deprecado mas ativo)
    - GET /materia/relatorias/{codigo}      (enriquecimento, melhor esforço)

Referências:
    - https://legis.senado.leg.br/dadosabertos/api-docs/swagger-ui/index.html
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

import httpx
from loguru import logger

from relmeg_core.connectors.base_connector import (
    _data_br,
    extrair_linha_tempo,
    LegislativoConnector,
)
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel
from relmeg_core.utils.helpers import para_int, parse_date_iso

URL_BASE = "https://legis.senado.leg.br/dadosabertos"
# Página pública da matéria — fallback de raspagem quando a API falha.
URL_MATERIA_PUBLICA = "https://www25.senado.leg.br/web/atividade/materias/-/materia"


def _sem_erro(valor: Any) -> Optional[Dict[str, Any]]:
    """Converte exceção do gather em None (enriquecimento best-effort)."""
    if isinstance(valor, BaseException):
        return None
    return valor

# Compat: helpers compartilhados (módulo relmeg_core.utils.helpers).
_para_int = para_int
_data_iso = parse_date_iso


def _lista(valor: Any) -> List[Any]:
    """Normaliza elemento único ou lista da resposta (XML/JSON do Senado)."""
    if valor is None:
        return []
    if isinstance(valor, list):
        return valor
    return [valor]


def _navegar(alvo: Any, *caminho: str) -> Any:
    """Desce por chaves de forma tolerante; retorna None se algo faltar."""
    atual = alvo
    for chave in caminho:
        if not isinstance(atual, dict):
            return None
        atual = atual.get(chave)
    return atual


class SenadoConnector(LegislativoConnector):
    """Conector do Senado Federal (API oficial Dados Abertos)."""

    fonte = "senado"
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
        resposta = await self._http_get_json(f"{URL_BASE}/senador/{id_externo}")
        registro = _navegar(resposta, "DetalheParlamentar", "Parlamentar") or {}
        ident = registro.get("IdentificacaoParlamentar") or {}

        return ParlamentarModel(
            fonte=self.fonte,
            url_origem=f"{URL_BASE}/senador/{id_externo}",
            id_externo=str(ident.get("CodigoParlamentar") or id_externo),
            nome_completo=ident.get("NomeCompletoParlamentar") or ident.get("NomeParlamentar") or "Indisponível",
            partido=ident.get("SiglaPartidoParlamentar")
            or _navegar(registro, "Mandato", "SiglaPartidoParlamentar"),
            uf=ident.get("UfParlamentar") or "BR",
            nome_urna=ident.get("NomeParlamentar"),
            status_ativo=True,
            cargo="Senador",
            email=ident.get("EmailParlamentar"),
            url_foto=ident.get("UrlFotoParlamentar"),
            url_perfil=ident.get("UrlPaginaParlamentar"),
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
        # Autoria e histórico são parte do enriquecimento padrão; quem restringe
        # campos pode executar sem eles se não estiverem no pedido.
        enriquece = campos is None or any(
            c in {"autor_principal", "integrantes", "situacao", "comissao_atual", "relator"} for c in campos
        )
        tarefas: List[Any] = [self._http_get_json(f"{URL_BASE}/materia/{id_externo}")]
        if enriquece:
            tarefas += [
                self._http_get_json(f"{URL_BASE}/materia/autoria/{id_externo}"),
                self._http_get_json(f"{URL_BASE}/materia/movimentacoes/{id_externo}"),
                self._http_get_json(f"{URL_BASE}/materia/relatorias/{id_externo}"),
            ]
        else:
            tarefas += [None, None, None]
        detalhe, resposta_autoria, resposta_movimentacoes, resposta_relatorias = (
            await asyncio.gather(*tarefas, return_exceptions=True)
        )
        if isinstance(detalhe, BaseException):
            raise detalhe
        resposta_autoria = _sem_erro(resposta_autoria)
        resposta_movimentacoes = _sem_erro(resposta_movimentacoes)
        resposta_relatorias = _sem_erro(resposta_relatorias)

        registro = _navegar(detalhe, "DetalheMateria", "Materia") or {}
        ident = registro.get("IdentificacaoMateria") or {}
        basicos = registro.get("DadosBasicosMateria") or {}

        autores = self._extrair_autores(resposta_autoria)
        relator = self._extrair_relator(resposta_relatorias) if resposta_relatorias else None
        ultima = self._ultima_movimentacao(resposta_movimentacoes) if resposta_movimentacoes else None
        situacao = ultima["situacao"] if ultima else None
        comissao = ultima["local"] if ultima else None

        return ProjetoDeLeiModel(
            fonte=self.fonte,
            url_origem=f"{URL_BASE}/materia/{id_externo}",
            id_externo=str(ident.get("CodigoMateria") or id_externo),
            sigla_tipo=ident.get("SiglaSubtipoMateria") or "PL",
            numero=_para_int(ident.get("NumeroMateria")),
            ano=_para_int(ident.get("AnoMateria")),
            ementa=basicos.get("EmentaMateria") or "Ementa indisponível",
            orgao_origem="senado",
            url_documento=None,
            autor_principal=autores[0] if autores else None,
            integrantes=autores,
            pauta_tematica=self._pauta_tematica(registro),
            situacao=situacao,
            comissao_atual=comissao,
            relator=relator,
            data_apresentacao=_data_iso(basicos.get("DataApresentacao")),
            status_sn=ident.get("IndicadorTramitando"),
        )

    @staticmethod
    def _extrair_autores(resposta_autoria: Optional[Dict[str, Any]]) -> List[str]:
        """Autores da matéria (lista de ``Autor`` com ``NomeAutor``, melhor esforço)."""
        if not resposta_autoria:
            return []
        autor = _navegar(resposta_autoria, "AutoriaMateria", "Materia", "Autoria", "Autor")
        nomes: List[str] = []
        for item in _lista(autor):
            nome = item.get("NomeAutor") if isinstance(item, dict) else None
            if nome and str(nome).strip():
                nomes.append(str(nome).strip())
        return nomes

    @staticmethod
    def _extrair_relator(resposta_relatorias: Optional[Dict[str, Any]]) -> Optional[str]:
        """Relator da matéria (enriquecimento best-effort; tolerante a schema)."""
        if not resposta_relatorias:
            return None
        relatoria = _navegar(resposta_relatorias, "RelatoriaMateria", "Materia", "Relatoria")
        for item in _lista(relatoria):
            relator = _navegar(item, "Relator", "IdentificacaoParlamentar", "NomeParlamentar")
            if not relator:
                relator = _navegar(item, "Relator", "NomeParlamentar")
            if relator and str(relator).strip():
                return str(relator).strip()
        return None

    @staticmethod
    def _pauta_tematica(registro: Dict[str, Any]) -> Optional[str]:
        """Assunto/Indexação da matéria em texto (se a fonte expuser)."""
        indexacao = _navegar(registro, "DadosBasicosMateria", "IndexacaoMateria")
        if indexacao is None:
            indexacao = _navegar(registro, "Assunto", "AssuntoEspecifico", "Descricao")
        if isinstance(indexacao, list):
            trechos = [str(v).strip() for v in indexacao if str(v or "").strip()]
            return ", ".join(trechos) or None
        return str(indexacao).strip() or None if indexacao else None

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
            resposta = await self._http_get_json(f"{URL_BASE}/materia/movimentacoes/{id_externo}")
            eventos = self._extrair_informes(resposta)
        except httpx.HTTPError:
            logger.warning(
                "senado: API de movimentações indisponível para {id}; tentando a página pública",
                id=id_externo,
            )
            eventos = []

        if eventos:
            return self._mapear_informes(eventos, id_externo)

        logger.info("senado: sem movimentações na API para {id}; tentando a página pública", id=id_externo)
        return await self._tramitacoes_via_pagina(id_externo)

    @staticmethod
    def _extrair_informes(resposta: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Informes legislativos (histórico) da resposta de movimentações."""
        autuacoes = _navegar(
            resposta, "MovimentacaoMateria", "Materia", "Autuacoes", "Autuacao"
        )
        eventos: List[Dict[str, Any]] = []
        for autuacao in _lista(autuacoes):
            informes = _navegar(autuacao, "InformesLegislativos", "InformeLegislativo")
            eventos.extend(_lista(informes))
        return eventos

    @staticmethod
    def _mapear_informes(eventos: List[Dict[str, Any]], id_externo: str) -> List[TramitacaoModel]:
        """Normaliza os informes em TramitacaoModel (ordem cronológica)."""
        eventos_ordenados = sorted(eventos, key=lambda e: _data_iso(_navegar(e, "Data")) or date.min)
        tramitacoes: List[TramitacaoModel] = []
        for sequencia, evento in enumerate(eventos_ordenados, start=1):
            local = _navegar(evento, "Local") or {}
            descricao = _navegar(evento, "Descricao")
            tramitacoes.append(
                TramitacaoModel(
                    fonte="senado",
                    url_origem=f"{URL_BASE}/materia/movimentacoes/{id_externo}",
                    id_proposicao_externo=str(id_externo),
                    data_evento=_data_iso(_navegar(evento, "Data")),
                    orgao_local=local.get("NomeLocal") or local.get("SiglaLocal"),
                    descricao_fase=descricao,
                    status=descricao or "Indisponível",
                    sequencia=sequencia,
                    despacho=None,
                    url_evento=None,
                )
            )
        return tramitacoes

    async def _tramitacoes_via_pagina(self, id_externo: str) -> List[TramitacaoModel]:
        """Fallback: raspa o texto da página pública da matéria e extrai a
        linha do tempo (seção 'Tramitação'). Devolve [] sem quebrar se o
        markup mudar ou a página falhar."""
        extraido = await self._raspar(
            f"{URL_MATERIA_PUBLICA}/{id_externo}", {"texto": "body"}
        )
        passos = extrair_linha_tempo((extraido or {}).get("texto") or "", marcador="Tramita")
        return [
            TramitacaoModel(
                fonte=self.fonte,
                url_origem=f"{URL_MATERIA_PUBLICA}/{id_externo}",
                id_proposicao_externo=str(id_externo),
                data_evento=_data_br(data_txt),
                descricao_fase=descricao,
                status=descricao or "Indisponível",
                sequencia=sequencia,
                despacho=None,
                url_evento=None,
            )
            for sequencia, (data_txt, descricao) in enumerate(passos, start=1)
        ]

    @staticmethod
    def _ultima_movimentacao(resposta: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Última parte do histórico para alimentar situação/comissão do projeto."""
        autuacoes = _navegar(
            resposta, "MovimentacaoMateria", "Materia", "Autuacoes", "Autuacao"
        )
        ultimo: Optional[Dict[str, str]] = None
        for autuacao in _lista(autuacoes):
            informes = _navegar(autuacao, "InformesLegislativos", "InformeLegislativo")
            for evento in _lista(informes):
                descricao = _navegar(evento, "Descricao")
                local = _navegar(evento, "Local") or {}
                ultimo = {
                    "situacao": descricao,
                    "local": local.get("NomeLocal") or local.get("SiglaLocal"),
                }
        return ultimo