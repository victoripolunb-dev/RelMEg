"""
Testes do Orquestrador Legislativo (relmeg_core) — sem rede, DB temporário.

Valida o contrato do motor: resolue fonte → coleta → persiste atomicamente na
base relmeg → audita, tudo sob demanda (sem agendamento).
"""
import asyncio
from unittest.mock import AsyncMock, patch

# Sem override de RELMEG_CACHE_DB aqui: o conftest.py já aponta o cache para um
# tempdir ANTES de qualquer import. Mutar os.environ no import deste módulo
# desfazia o isolamento caso ele seja importado antes do config.py.
import database as _db  # noqa: E402  (módulo-irmão do backend, no sys.path)
from relmeg_core.connectors.camara import CamaraConnector  # noqa: E402
from relmeg_core.orquestrador import (  # noqa: E402
    REGISTRO_CONECTORES,
    OrquestradorLegislativo,
)
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


def test_fontes_estaduais_nao_sao_registradas():
    """Escopo federal (decisão de 01/10/2026): CLDF/ALGO/ALMG/ALESP saíram."""
    orch = OrquestradorLegislativo()
    assert set(REGISTRO_CONECTORES) == {"camara", "senado", "dou"}

    async def principal():
        return await orch.coletar_projeto("cldf", "PL 2473/2026")

    try:
        asyncio.run(principal())
        assert False, "esperava ValueError"
    except ValueError as exc:
        assert "cldf" in str(exc)


def test_fontes_disponiveis_reflete_status():
    orch = OrquestradorLegislativo()
    status = orch.fontes_disponiveis()
    assert status["camara"] == "pronta"
    assert status["senado"] == "pronta"
    assert set(status) == {"camara", "senado", "dou"}