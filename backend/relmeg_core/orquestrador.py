"""
Orquestrador Legislativo — motor universal de inteligência (relmeg_core).

Dispara conectores sob demanda (nunca em background): resolve a fonte canônica,
executa a extração, normaliza para os modelos universais e persiste na base
relmeg (``database.py``). O rate limit (slowapi) é responsabilidade da ROTA de
disparo — aqui nada agenda, cron ou varre em segundo plano (AGENTS.md).

Fluxo típico de uma requisição:
    1. router.py aplica ``@limiter.limit(...)`` (disparo on-demand);
    2. ``orquestrador.coletar_completo("senado", id)``;
    3. persistência atômica em relmeg_proposicoes + relmeg_tramitacoes;
    4. registra o evento na auditoria (tabela ``auditoria_eventos``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from relmeg_core.connectors import (
    AlespConnector,
    AlgoConnector,
    AlmgConnector,
    CamaraConnector,
    CldfConnector,
    DouConnector,
    FonteSemApiPublica,
    LegislativoConnector,
    SenadoConnector,
)
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel


def _modulo_database():
    """Import tardio de ``database`` (módulo-irmão do app, fora do pacote)."""
    import database  # noqa: PLC0415  (import local para isolar dependência)

    return database


# Fonte canônica → classe conectora. Adicionar novas fontes = registrar aqui.
REGISTRO_CONECTORES: Dict[str, type] = {
    "camara": CamaraConnector,
    "senado": SenadoConnector,
    "cldf": CldfConnector,
    "dou": DouConnector,
    "algo": AlgoConnector,
    "almg": AlmgConnector,
    "alesp": AlespConnector,
}


class OrquestradorLegislativo:
    """Serviço que resolve e executa a extração delegando aos conectores."""

    def __init__(self) -> None:
        self._instancias: Dict[str, LegislativoConnector] = {}

    # ------------------------------------------------------------------
    # Resolução de conector
    # ------------------------------------------------------------------

    def conector(self, fonte: str) -> LegislativoConnector:
        """Retorna a instância do conector para a fonte (cache de instâncias)."""
        fonte = str(fonte).strip().lower()
        if fonte not in REGISTRO_CONECTORES:
            raise ValueError(
                f"Fonte '{fonte}' desconhecida. Disponíveis: {', '.join(sorted(REGISTRO_CONECTORES))}"
            )
        if fonte not in self._instancias:
            self._instancias[fonte] = REGISTRO_CONECTORES[fonte]()
        return self._instancias[fonte]

    def fontes_disponiveis(self) -> Dict[str, str]:
        """Status das fontes registradas (para orquestrar a UI do operador).

        O texto vem do atributo ``status_v1`` de cada conector (reflete o
        estágio real na V1: pronta, pronta-(busca), indisponível, mapeamento
        futuro...). Nenhuma chamada externa aqui.
        """
        status: Dict[str, str] = {}
        for fonte, classe in REGISTRO_CONECTORES.items():
            status[fonte] = getattr(classe, "status_v1", "pronta")
        return status

    # ------------------------------------------------------------------
    # Coleta (sempre sob demanda, com persistência opcional)
    # ------------------------------------------------------------------

    async def coletar_parlamentar(
        self,
        fonte: str,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        persisitir: bool = True,
    ) -> ParlamentarModel:
        fonte = str(fonte).strip().lower()
        conn = self.conector(fonte)
        try:
            parlamentar = await conn.obter_parlamentar(id_externo, campos=campos)
        except FonteSemApiPublica:
            logger.warning("orquestrador: fonte {f} sem API (- {detalhe})", f=fonte, detalhe="ALGO")
            raise
        if persisitir:
            _modulo_database().salvar_parlamentar(parlamentar)
            self._auditar(f"parlamentar {fonte}/{parlamentar.id_externo} salvo")
        return parlamentar

    async def coletar_projeto(
        self,
        fonte: str,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        persisitir: bool = True,
    ) -> ProjetoDeLeiModel:
        fonte = str(fonte).strip().lower()
        conn = self.conector(fonte)
        projeto = await conn.obter_projeto(id_externo, campos=campos)
        if persisitir:
            _modulo_database().salvar_projeto(projeto)
            self._auditar(f"projeto {fonte}/{projeto.id_externo} salvo")
        return projeto

    async def coletar_tramitacoes(
        self,
        fonte: str,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
        persisitir: bool = True,
    ) -> List[TramitacaoModel]:
        fonte = str(fonte).strip().lower()
        conn = self.conector(fonte)
        tramitacoes = await conn.obter_tramitacoes(id_externo, campos=campos)
        if persisitir:
            _modulo_database().salvar_tramitacoes(fonte, str(id_externo), tramitacoes)
            self._auditar(f"tramitações {fonte}/{id_externo}: {len(tramitacoes)} eventos")
        return tramitacoes

    async def coletar_completo(
        self,
        fonte: str,
        id_externo: str,
        *,
        campos: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Projeto + tramitação numa requisição, persistidos atomicamente.

        Em fontes sem histórico (CLDF), a tramitação pode vir vazia ou com o
        estágio atual — o retorno é sempre um dicionário com os dois modelos.
        """
        fonte = str(fonte).strip().lower()
        conn = self.conector(fonte)
        projeto = await conn.obter_projeto(id_externo, campos=campos)
        try:
            tramitacoes = await conn.obter_tramitacoes(id_externo, campos=campos)
        except FonteSemApiPublica:
            tramitacoes = []

        _modulo_database().salvar_projeto(projeto)
        _modulo_database().salvar_tramitacoes(fonte, projeto.id_externo, tramitacoes)
        self._auditar(
            f"coleta completa {fonte}/{projeto.id_externo}: "
            f"{len(tramitacoes)} tramitações"
        )
        return {
            "fonte": fonte,
            "id_externo": projeto.id_externo,
            "projeto": projeto,
            "tramitacoes": tramitacoes,
        }

    async def buscar_proposicoes(
        self,
        fonte: str,
        termo: str,
        *,
        sigla_tipo: Optional[str] = None,
        ano: Optional[int] = None,
        itens: int = 25,
    ) -> List[Dict[str, Any]]:
        """Busca proposições por palavras-chave (chamada externa sob demanda).

        Retorna resumos normalizados SEM persistir; o operador decide, por
        resultado, se dispara a coleta completa (POST /hub/proposicoes/...).
        Fontes sem busca via API levantam ``BuscaNaoSuportada`` (501).
        """
        fonte = str(fonte).strip().lower()
        conn = self.conector(fonte)
        return await conn.buscar_proposicoes(
            termo, sigla_tipo=sigla_tipo, ano=ano, itens=itens
        )

    # ------------------------------------------------------------------
    # Auditoria
    # ------------------------------------------------------------------

    @staticmethod
    def _auditar(detalhe: str) -> None:
        """Registra o evento na auditoria sem nunca quebrar o fluxo."""
        try:
            _modulo_database().registrar_evento("relmeg_core", detalhe)
        except Exception as exc:  # noqa: BLE001
            logger.debug("orquestrador: auditoria indisponível: {e}", e=exc)


_orquestrador = OrquestradorLegislativo()


def obter_orquestrador() -> OrquestradorLegislativo:
    """Singleton do orquestrador (as rotas disparam apenas este)."""
    return _orquestrador