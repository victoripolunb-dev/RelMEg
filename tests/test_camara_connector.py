"""
Testes do conector da Câmara (relmeg_core) — fixtures sem rede.

Garantem o contrato do Padrão Adapter: payloads reais (formato da API v2)
viram SEMPRE modelos Pydantic válidos, sem depender de rede durantes os testes.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from relmeg_core.connectors.camara import CamaraConnector
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

FIXTURE_DEPUTADO = {
    "dados": {
        "id": 204554,
        "uri": "https://dadosabertos.camara.leg.br/api/v2/deputados/204554",
        "nomeCivil": "JOSE ABILIO SILVA DE SANTANA",
        "ultimoStatus": {
            "nome": "Ablio Santana",
            "nomeEleitoral": "Ablio Santana",
            "siglaPartido": "PSC",
            "siglaUf": "ba",
            "condicaoEleitoral": "Titular",
            "email": None,
            "urlFoto": "https://www.camara.leg.br/internet/deputado/bandep/204554.jpg",
        },
    }
}

FIXTURE_PROPOSICAO = {
    "dados": {
        "id": 2571616,
        "siglaTipo": "REQ",
        "numero": 4264,
        "ano": 2025,
        "ementa": "Requerimento de inclusão de pauta.",
        "dataApresentacao": "2025-03-10T10:00",
        "keywords": ["pauta", "educação"],
        "urlInteiroTeor": "https://www.camara.leg.br/proposicoesWeb/prop_mostrarintegra?codteor=1",
        "statusProposicao": {
            "descricaoSituacao": None,
            "siglaOrgao": None,
            "despacho": "Recebimento pela Mesa",
        },
    }
}

FIXTURE_AUTORES = {"dados": [{"autor": "Dep. Ana Souza"}, {"autor": "Maria Lima"}]}

FIXTURE_TRAMITACOES = {
    "dados": [
        {
            "dataHora": "2025-03-08T14:00",
            "sequencia": 1,
            "siglaOrgao": "MESA",
            "descricaoTramitacao": "Apresentação de Proposição",
            "descricaoSituacao": "Aguardando Leitura",
            "despacho": "Apresentação de Proposição",
            "url": "https://www.camara.leg.br/tramitacao/1",
        },
        {
            "dataHora": "2025-03-10T11:00",
            "sequencia": 2,
            "siglaOrgao": "PLEN",
            "descricaoTramitacao": "Leitura da Matéria",
            "descricaoSituacao": None,
            "despacho": "Parecer do Relator, Dep. Rodrigo Martins (PSB-PI).",
            "url": "https://www.camara.leg.br/tramitacao/2",
        },
    ]
}


def _conector(fake_http):
    conector = CamaraConnector()
    patch.object(conector, "_http_get_json", new=fake_http).start()
    return conector


async def _fake_http_por_url(mapa: dict):
    async def _fake(url: str, *, params=None, headers=None):
        for sufixo, payload in mapa.items():
            if url.endswith(sufixo):
                return payload
        raise AssertionError(f"URL inesperada no teste: {url}")
    return _fake


def test_obter_parlamentar_mapeia_modelo():
    async def principal():
        conector = _conector(await _fake_http_por_url({"deputados/204554": FIXTURE_DEPUTADO}))
        dep = await conector.obter_parlamentar("204554")
        return dep

    dep = asyncio.run(principal())
    assert isinstance(dep, ParlamentarModel)
    assert dep.id_externo == "204554"
    assert dep.nome_completo == "JOSE ABILIO SILVA DE SANTANA"
    assert dep.nome_urna == "Ablio Santana"
    assert dep.partido == "PSC"
    assert dep.uf == "BA"
    assert dep.status_ativo is True
    assert dep.fonte == "camara"
    assert dep.url_origem.endswith("204554")


def test_obter_projeto_complementa_situacao_pela_tramitacao():
    async def principal():
        conector = _conector(
            await _fake_http_por_url(
                {
                    "proposicoes/2571616": FIXTURE_PROPOSICAO,
                    "proposicoes/2571616/autores": FIXTURE_AUTORES,
                    "proposicoes/2571616/tramitacoes": FIXTURE_TRAMITACOES,
                }
            )
        )
        return await conector.obter_projeto("2571616")

    projeto = asyncio.run(principal())
    assert isinstance(projeto, ProjetoDeLeiModel)
    assert projeto.sigla_tipo == "REQ"
    assert projeto.numero == 4264
    assert projeto.ano == 2025
    # situação vinda da API era nula → complementada pela última tramitação.
    assert projeto.situacao == "Leitura da Matéria"
    assert projeto.comissao_atual == "PLEN"
    assert projeto.relator == "Rodrigo Martins"
    assert projeto.integrantes == ["Dep. Ana Souza", "Maria Lima"]
    assert projeto.pauta_tematica == "pauta, educação"
    assert projeto.data_apresentacao is not None and projeto.data_apresentacao.year == 2025


def test_obter_tramitacoes_devolve_lista_ordenada():
    async def principal():
        conector = _conector(
            await _fake_http_por_url({"proposicoes/2571616/tramitacoes": FIXTURE_TRAMITACOES})
        )
        return await conector.obter_tramitacoes("2571616")

    tramitacoes = asyncio.run(principal())
    assert isinstance(tramitacoes, list)
    assert len(tramitacoes) == 2
    assert all(isinstance(t, TramitacaoModel) for t in tramitacoes)
    primera, segunda = tramitacoes
    assert primera.sequencia == 1
    assert primera.status == "Aguardando Leitura"
    assert segunda.status == "Leitura da Matéria"
    assert segunda.orgao_local == "PLEN"