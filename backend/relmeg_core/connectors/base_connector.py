"""
Interface base dos conectores legislativos (Padrão Adapter).

Todo conector (Câmara, Senado, CLDF, ALGO, DOU...) herda de
``LegislativoConnector`` e traduz os dados da sua fonte — JSON oficial ou HTML
raspado — para os Modelos Pydantic de ``relmeg_core.models.schemas``. O resto
do sistema nunca vê o formato da fonte, apenas o modelo normalizado.

Contrato de coleta (decisão do operador, 10/09/2026):
    - PADRÃO: se nenhum campo for restringido, preservar o payload integral da
      fonte (volume completo, "como está"); o conector só enriquece o modelo.
    - RESTRITO: se o operador informar ``campos=[...]``, devolver apenas esses
      campos (busca seletiva), reduzindo volume e trabalho de enriquecimento.
"""
from __future__ import annotations

import asyncio
import random
import re
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

import httpx
from loguru import logger

from config import settings
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

CORPO_ERRO_HTTP = "Falha de requisição HTTP no conector {fonte}: {detalhe}"

# Códigos de status que JUSTIFICAM retry (falhas transitórias de servidor/limite
# de taxa/bloqueio temporário). Erros definitivos (400, 401, 404, 422...) sobem
# imediatamente sem gastar tentativas — retentar 404 é desperdício e atrasa a
# resposta ao operador.
_HTTP_RETRYAVEIS = {403, 408, 429, 500, 502, 503, 504}


class BuscaNaoSuportada(NotImplementedError):
    """Fonte não oferece busca por palavras-chave via API pública.

    Mapeada para 501 nas rotas do hub — o operador ainda pode coletar
    proposições conhecidas pelo ID (POST /hub/proposicoes/{fonte}/{id}).
    """


class FonteSemApiPublica(RuntimeError):
    """A fonte ainda não expõe API pública consumível pelo motor (ou o
    mapeamento dela ainda não foi implementado na V1). Mapeada para 501."""


def filtrar_campos(payload: Dict[str, Any], campos: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Aplica o contrato de coleta: volume completo por padrão, seleção se pedida.

    - ``campos=None`` → devolve o payload integral (default, "como está").
    - ``campos=["a","b"]`` → devolve apenas esses campos presentes no payload.
    """
    if not campos:
        return payload
    return {chave: payload[chave] for chave in campos if chave in payload}


def _scrapling_instalado() -> bool:
    """True se o pacote Scrapling estiver disponível para o fallback de raspagem.

    Import LAZY: falha de importação aqui NÃO derruba a aplicação; os conectores
    apenas seguem pela caminho da API oficial quando o estepe não existe.
    """
    try:
        from relmeg_core.utils import scrapling_engine as _se  # type: ignore[import-not-found]

        _se._carregar_scrapling()
        return True
    except Exception:  # noqa: BLE001
        return False


def _data_br(valor: Any) -> Optional[date]:
    """Converte data brasileira 'dd/mm/aaaa' (com ou sem hora) para ``date``."""
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor)[:10].strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def extrair_linha_tempo(texto: str, marcador: str = "andamento") -> List[tuple]:
    """Extrai pares ``(data_br, descrição)`` de um texto de linha do tempo.

    Heurística tolerante a markup, usada nos fallbacks de raspagem (CLDF e
    Senado) quando a fonte renderiza o histórico via JS: localiza o trecho a
    partir de ``marcador`` e quebra os passos pelos padrões de data dd/mm/aaaa,
    tomando como descrição o texto até a próxima data.
    """
    plano = re.sub(r"[ \t\r\n]+", " ", str(texto or ""))
    pos = plano.lower().find(str(marcador).lower())
    if pos >= 0:
        plano = plano[pos:]
    padrao = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s+\d{2}:\d{2})?")
    marcas = [(m.start(), m) for m in padrao.finditer(plano)]
    passos: List[tuple] = []
    for i, (inicio, m) in enumerate(marcas):
        fim = marcas[i + 1][0] if i + 1 < len(marcas) else len(plano)
        descricao = " ".join(plano[m.end():fim].split())
        descricao = descricao.strip(" ,.;:-–—|·")
        if len(descricao) > 3:
            passos.append((m.group(1), descricao))
    return passos


class LegislativoConnector(ABC):
    """Classe abstrata herdada por todos os conectores do RelMeg.

    Atributos de classe que cada conector deve sobreescrever:
        - ``fonte``:  identificador canônico ("camara", "senado", "cldf", "algo"...);
        - ``url_raiz``: base pública da fonte (usada para montar URLs).
    """

    fonte: str = "indefinido"
    url_raiz: str = ""

    # Status exibido por ``ORQUESTRADOR.fontes_disponiveis`` (GET /hub/fontes).
    # Cada fonte sobreescreve com seu estágio real na V1 (ex.: "pronta (busca)",
    # "mapeamento futuro (API v2)", "indisponível (sem API pública)").
    status_v1: str = "pronta"

    # Fallback por raspagem (estepe quando a API oficial falha/fica vazia).
    # Ligado globalmente por config (env USA_SCRAPLING); cada conector pode
    # desligar localmente. O uso é sempre sob demanda (disparo do operador).
    usa_scrapling: bool = settings.usa_scrapling

    # Parâmetros de resiliência (padrão vindos de config.py, sobreescrevíveis).
    timeout: float = settings.http_timeout
    max_tentativas: int = settings.http_max_tentativas
    backoff_base: float = settings.http_backoff_base
    backoff_jitter: float = settings.http_backoff_jitter

    # ------------------------------------------------------------------
    # API obrigatória (cada fonte implementa seu mapeamento)
    # ------------------------------------------------------------------

    @abstractmethod
    async def obter_parlamentar(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ParlamentarModel:
        """Retorna um parlamentar normalizado. ``campos`` restringe se informado."""

    @abstractmethod
    async def obter_projeto(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> ProjetoDeLeiModel:
        """Retorna uma proposição normalizada. ``campos`` restringe se informado."""

    @abstractmethod
    async def obter_tramitacoes(
        self,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        **opcoes: Any,
    ) -> List[TramitacaoModel]:
        """Retorna o histórico de tramitação normalizado (lista, possivelmente vazia)."""

    async def buscar_proposicoes(
        self,
        termo: str,
        *,
        sigla_tipo: Optional[str] = None,
        ano: Optional[int] = None,
        itens: int = 25,
        **opcoes: Any,
    ) -> List[Dict[str, Any]]:
        """Busca proposições por palavras-chave (resumos normalizados, sem persistir).

        Padrão: fonte não implementa busca por keywords → BuscaNaoSuportada (501).
        Fontes que oferecem busca (ex.: Câmara) sobrescrevem este método.
        """
        raise BuscaNaoSuportada(
            f"A fonte '{self.fonte}' não oferece busca por palavras-chave via API pública "
            "no motor. Colete proposições conhecidas pelo ID (POST /hub/proposicoes)."
        )

    # ------------------------------------------------------------------
    # Helpers compartilhados (HTTP com retry + backoff)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _cliente(
        self,
        headers: Optional[Dict[str, str]] = None,
    ) -> AsyncIterator[httpx.AsyncClient]:
        """Cliente HTTP assíncrono com timeout e headers padrão do motor."""
        cabecalhos = {
            "Accept": "application/json",
            "User-Agent": settings.http_user_agent,
        }
        if headers:
            cabecalhos.update(headers)
        timeout = httpx.Timeout(self.timeout)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=cabecalhos,
        ) as client:
            yield client

    async def _http_get_json(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """GET JSON com retry exponencial + jitter (403/429/5xx e erros de rede)."""
        return await self._tentar(
            lambda client, url=url, params=params: client.get(url, params=params),
            headers=headers,
        )

    async def _http_post_json(
        self,
        url: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """POST JSON com retry exponencial + jitter; corpos podem ser ``dict`` ou listas."""
        return await self._tentar(
            lambda client, url=url, json=json, params=params: client.post(url, json=json, params=params),
            headers=headers,
        )

    async def _raspar(
        self,
        url: str,
        seletores: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Fallback por raspagem via Scrapling (best-effort, sob demanda).

        Devolve dict ``{campo: valor}`` conforme os ``seletores`` (CSS/XPath,
        formato do ``scrapling_engine``) ou ``None`` se o fallback estiver
        desligado, o Scrapling não estiver instalado ou a página falhar.
        Nunca levanta: o conector decide continuar com a API oficial.
        """
        if not self.usa_scrapling or not _scrapling_instalado():
            return None
        try:
            from relmeg_core.utils import scrapling_engine as _se

            return await asyncio.to_thread(
                _se.extrair_html_simples,
                url,
                seletores,
                timeout_ms=settings.scrapling_timeout_ms,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "scrapling: fallback falhou para {url}: {e}",
                url=url,
                e=exc,
            )
            return None

    async def _tentar(
        self,
        acao,
        *,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Núcleo do retry: executa ``acao`` (função assíncrona sobre o client).

        Retenta APENAS falhas transitórias: erros de rede/timeout e códigos
        HTTP retryáveis (``_HTTP_RETRYAVEIS``). 4xx definitivos (404, 400, 422)
        e 5xx irreversíveis sobem na primeira tentativa.
        """
        tentativa = 0
        while True:
            tentativa += 1
            try:
                async with self._cliente(headers) as client:
                    resposta = await acao(client)
                    if resposta.status_code >= 400:
                        if (
                            resposta.status_code in _HTTP_RETRYAVEIS
                            and tentativa < self.max_tentativas
                        ):
                            await self._aguardar_retry(tentativa, exc=resposta.status_code)
                            continue
                        logger.warning(
                            CORPO_ERRO_HTTP,
                            fonte=self.fonte,
                            detalhe=f"HTTP {resposta.status_code}",
                        )
                        resposta.raise_for_status()
                    return resposta.json()
            except httpx.TransportError as exc:
                if tentativa >= self.max_tentativas:
                    logger.warning(CORPO_ERRO_HTTP, fonte=self.fonte, detalhe=exc)
                    raise
                await self._aguardar_retry(tentativa, exc=exc)

    async def _aguardar_retry(self, tentativa: int, exc: Any) -> None:
        """Backoff exponencial + jitter entre tentativas de retry."""
        atraso = self.backoff_base * (2 ** (tentativa - 1))
        atraso += random.uniform(0, self.backoff_jitter)
        logger.debug(
            "Conector {fonte}: retry {t}/{m} após {s:.2f}s ({detalhe})",
            fonte=self.fonte,
            t=tentativa,
            m=self.max_tentativas,
            s=atraso,
            detalhe=exc,
        )
        await asyncio.sleep(atraso)

    @staticmethod
    def _fonte_mesma_fonte(id_externo: str, fonte: str) -> str:
        """Compat: normaliza o id externo para grafia canônica da fonte."""
        return str(id_externo).strip()