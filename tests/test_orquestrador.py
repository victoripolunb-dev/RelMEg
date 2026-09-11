"""
Testes do Orquestrador Legislativo (relmeg_core) — sem rede, DB temporário.

Valida o contrato do motor: resolue fonte → coleta → persiste atomicamente na
base relmeg → audita, tudo sob demanda (sem agendamento).
"""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Banco isolado para o teste — precisa ser definido antes do import de config.
os.environ["RELMEG_CACHE_DB"] = str(
    Path(__file__).parent / "tmp_test_orquestrador.sqlite"
)

import database as _db  # noqa: E402  (módulo-irmão do backend, no sys.path)
from relmeg_core.connectors.camara import CamaraConnector  # noqa: E402
from relmeg_core.orquestrador import OrquestradorLegislativo  # noqa: E402
from relmeg_core.models.schemas import (  # noqa: E402
    ParlamentarModel,
    ProjetoDeLeiModel,
    TramitacaoModel,
)


PROJETO_FIXTURE = ProjetoDeLeiModel(
    fonte="camara",
    url_origem="https://dadosabertos.camara.leg.br/api/v2/proposicoes/2571616",
    id_externo="2571616",
    sigla_tipo="PL",
    numero=4264,
    ano=2025,
    ementa="Requisição de inclusão de pauta.",
    orgao_origem="camara",
    autor_principal="Dep. Ana Souza",
    integrantes=["Dep. Ana Souza"],
)

TRAMITACAO_FIXTURE = TramitacaoModel(
    fonte="camara",
    url_origem="https://",
    id_proposicao_externo="2571616",
    data_evento=None,
    orgao_local="PLEN",
    descricao_fase="Leitura da Matéria",
    status="Leitura da Matéria",
    sequencia=1,
)

PARLAMENTAR_FIXTURE = ParlamentarModel(
    fonte="camara",
    url_origem="https://dadosabertos.camara.leg.br/api/v2/deputados/204554",
    id_externo="204554",
    nome_completo="JOSE ABILIO SILVA DE SANTANA",
    partido="PSC",
    uf="BA",
)


def _orquestrador_com_conector_fake() -> OrquestradorLegislativo:
    conector = CamaraConnector()
    patch.object(conector, "obter_projeto", new=AsyncMock(return_value=PROJETO_FIXTURE)).start()
    patch.object(conector, "obter_tramitacoes", new=AsyncMock(return_value=[TRAMITACAO_FIXTURE])).start()
    patch.object(conector, "obter_parlamentar", new=AsyncMock(return_value=PARLAMENTAR_FIXTURE)).start()

    orch = OrquestradorLegislativo()
    orch._instancias["camara"] = conector  # injeta a instância fake
    return orch


def test_coletar_completo_persiste_e_audita():
    async def principal():
        orch = _orquestrador_com_conector_fake()
        resultado = await orch.coletar_completo("camara", "2571616")
        return resultado

    resultado = asyncio.run(principal())
    assert resultado["fonte"] == "camara"
    assert resultado["projeto"].id_externo == "2571616"

    salvo = _db.buscar_projeto("camara", "2571616")
    assert salvo is not None
    assert salvo["sigla_tipo"] == "PL"
    assert _db.buscar_tramitacoes("camara", "2571616") != []

    eventos = _db.listar_eventos()
    assert any("coleta completa" in e["detalhe"] for e in eventos)


def test_coletar_parlamentar_persiste():
    async def principal():
        orch = _orquestrador_com_conector_fake()
        return await orch.coletar_parlamentar("camara", "204554")

    parlamentar = asyncio.run(principal())
    assert parlamentar.nome_completo.startswith("JOSE")
    salvo = _db.buscar_parlamentar("camara", "204554")
    assert salvo is not None and salvo["uf"] == "BA"


def test_fonte_desconhecida_erro_claro():
    async def principal():
        orch = OrquestradorLegislativo()
        return await orch.coletar_projeto("mingau", "1")

    try:
        asyncio.run(principal())
        assert False, "esperava ValueError"
    except ValueError as exc:
        assert "mingau" in str(exc)


def test_algo_sinaliza_sem_api():
    from relmeg_core.connectors.algo import FonteSemApiPublica

    async def principal():
        orch = OrquestradorLegislativo()
        return await orch.coletar_projeto("algo", "PL 1/2025")

    try:
        asyncio.run(principal())
        assert False, "esperava FonteSemApiPublica"
    except FonteSemApiPublica:
        pass


def test_fontes_disponiveis_reflete_status():
    orch = OrquestradorLegislativo()
    status = orch.fontes_disponiveis()
    assert status["camara"] == "pronta"
    assert status["senado"] == "pronta"
    assert status["cldf"] == "pronta"
    assert "indisponível" in status["algo"]