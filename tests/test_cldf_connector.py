"""
Testes do conector da CLDF (relmeg_core) — fixtures sem rede.

Espelham o contrato do Padrão Adapter com o payload real do PLE/CLDF
(campos tipoProposicao, siglaNumeroAno, etapa, autoria...). Inclui o case do
limite real: a API pública da CLDF não expõe histórico de tramitação.
"""
import asyncio
from unittest.mock import patch

from relmeg_core.connectors.cldf import CldfConnector
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

FIXTURE_ITEM = {
    "id": 159224,
    "tipoProposicao": "Projeto de Lei",
    "siglaNumeroAno": "PL 2473/2026",
    "descricaoProposicao": "2473/2026 - Projeto de Lei",
    "ementa": "Dispõe sobre a promoção da equidade de gênero na composição de equipes técnicas.",
    "autoria": "Deputada Dayse Amarilio",
    "etapa": "Apresentação",
    "situacaoProposicao": None,
    "parecer": None,
    "dataLeitura": "2026-09-10",
    "temaNome": "#Cultura#Mulher#",
    "regiaoAdministrativaNome": "#DISTRITO FEDERAL (INTEIRO)#",
    "ano": None,
}

FIXTURE_FILTER = {"content": [FIXTURE_ITEM], "totalElements": 1}

FIXTURE_CATALOGO = [
    {"id": 36, "nome": "Deputado Valdelino Barcelos", "situacao": "ATIVO", "tipoAutor": "PARLAMENTAR"},
    {"id": 6, "nome": "Poder Executivo", "situacao": "ATIVO", "tipoAutor": "ORGAO_EXTERNO"},
]


async def _fake_http(mapa: dict):
    async def _fake(url: str, *, params=None, headers=None, **rest):
        payload = mapa.get(url)
        if payload is None:
            raise AssertionError(f"URL inesperada no teste: {url}")
        return payload
    return _fake


def test_obter_projeto_mapeia_modelo():
    async def principal():
        conector = CldfConnector()
        patch.object(
            conector,
            "_http_post_json",
            new=await _fake_http({"https://ple.cl.df.gov.br/pleservico/api/public/proposicao/filter": FIXTURE_FILTER}),
        ).start()
        return await conector.obter_projeto("PL 2473/2026")

    projeto = asyncio.run(principal())
    assert isinstance(projeto, ProjetoDeLeiModel)
    assert projeto.sigla_tipo == "PL"
    assert projeto.numero == 2473
    assert projeto.ano == 2026
    assert projeto.ementa.startswith("Dispõe sobre")
    assert projeto.autor_principal == "Deputada Dayse Amarilio"
    assert projeto.integrantes == ["Deputada Dayse Amarilio"]
    assert projeto.situacao == "Apresentação"
    assert projeto.orgao_origem == "CLDF"
    assert projeto.fonte == "cldf"
    assert projeto.data_apresentacao is not None and projeto.data_apresentacao.year == 2026
    assert "Cultura" in (projeto.pauta_tematica or "")


def test_obter_tramitacoes_entrega_estagio_atual():
    async def principal():
        conector = CldfConnector()
        patch.object(
            conector,
            "_http_post_json",
            new=await _fake_http({"https://ple.cl.df.gov.br/pleservico/api/public/proposicao/filter": FIXTURE_FILTER}),
        ).start()
        return await conector.obter_tramitacoes("PL 2473/2026")

    tramitacoes = asyncio.run(principal())
    assert len(tramitacoes) == 1
    assert isinstance(tramitacoes[0], TramitacaoModel)
    assert tramitacoes[0].status == "Apresentação"


def test_obter_parlamentar_localiza_no_catalogo():
    async def principal():
        conector = CldfConnector()
        patch.object(
            conector,
            "_http_get_json",
            new=await _fake_http({"https://ple.cl.df.gov.br/pleservico/api/public/autor/listar": FIXTURE_CATALOGO}),
        ).start()
        return await conector.obter_parlamentar("36")

    dep = asyncio.run(principal())
    assert isinstance(dep, ParlamentarModel)
    assert dep.id_externo == "36"
    assert dep.nome_completo == "Deputado Valdelino Barcelos"
    assert dep.cargo == "Deputado Distrital"
    assert dep.uf == "DF"
    assert dep.status_ativo is True


def test_obter_parlamentar_inexistente_levanta_erro():
    async def principal():
        conector = CldfConnector()
        patch.object(
            conector,
            "_http_get_json",
            new=await _fake_http({"https://ple.cl.df.gov.br/pleservico/api/public/autor/listar": FIXTURE_CATALOGO}),
        ).start()
        return await conector.obter_parlamentar("notfound")

    try:
        asyncio.run(principal())
        assert False, "esperava ValueError"
    except ValueError:
        pass