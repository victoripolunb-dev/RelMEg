"""
Testes do conector do Senado (relmeg_core) — fixtures sem rede.

Espelham o contrato do Padrão Adapter: payloads reais (formato do Senado,
chaves AnoMateria/SiglaSubtipoMateria/movimentacoes...) viram SEMPRE modelos
Pydantic válidos, sem depender de rede durante os testes.
"""
import asyncio
from unittest.mock import patch

from relmeg_core.connectors.senado import SenadoConnector
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel, TramitacaoModel

FIXTURE_PARLAMENTAR = {
    "DetalheParlamentar": {
        "Parlamentar": {
            "IdentificacaoParlamentar": {
                "CodigoParlamentar": "5672",
                "NomeCompletoParlamentar": "Alan Rick Miranda",
                "NomeParlamentar": "Alan Rick",
                "SiglaPartidoParlamentar": "REPUBLICANOS",
                "UfParlamentar": "ac",
                "EmailParlamentar": "sen.alanrick@senado.leg.br",
                "UrlFotoParlamentar": "http://www.senado.leg.br/senadores/img/fotos-oficiais/senador5672.jpg",
                "UrlPaginaParlamentar": "http://www25.senado.leg.br/web/senadores/senador/-/perfil/5672",
            }
        }
    }
}

FIXTURE_DETALHE = {
    "DetalheMateria": {
        "Materia": {
            "IdentificacaoMateria": {
                "CodigoMateria": "166997",
                "SiglaSubtipoMateria": "PL",
                "NumeroMateria": "00003",
                "AnoMateria": "2025",
                "IndicadorTramitando": "Sim",
            },
            "DadosBasicosMateria": {
                "EmentaMateria": "Disciplina o processo estrutural.",
                "DataApresentacao": "2025-01-31",
                "IndexacaoMateria": "processo estrutural; jurisdição",
            },
        }
    }
}

FIXTURE_AUTORIA = {
    "AutoriaMateria": {
        "Materia": {
            "Autoria": {
                "Autor": [
                    {"NomeAutor": "Rodrigo Pacheco", "SiglaTipoAutor": "SENADOR", "UfAutor": "MG"}
                ]
            }
        }
    }
}

FIXTURE_MOVIMENTACOES = {
    "MovimentacaoMateria": {
        "Materia": {
            "Autuacoes": {
                "Autuacao": [
                    {
                        "NumeroAutuacao": "1",
                        "InformesLegislativos": {
                            "InformeLegislativo": [
                                {
                                    "Data": "2025-01-31 20:34:13",
                                    "Descricao": "Autuado o Projeto de Lei. Vai a publicação.",
                                    "Local": {"SiglaLocal": "SLSF", "NomeLocal": "Secretaria Legislativa"},
                                },
                                {
                                    "Data": "2025-02-06 09:10:00",
                                    "Descricao": "Aguardando distribuição.",
                                    "Local": {"SiglaLocal": "PLEN", "NomeLocal": "Plenário"},
                                },
                            ]
                        },
                    }
                ]
            }
        }
    }
}

FIXTURE_RELATORIAS = {"RelatoriaMateria": {"Materia": {"Relatoria": None}}}


def _conector(fake_http):
    conector = SenadoConnector()
    patch.object(conector, "_http_get_json", new=fake_http).start()
    return conector


async def _fake_http_por_url(mapa: dict):
    async def _fake(url: str, *, params=None, headers=None):
        for sufixo, payload in mapa.items():
            if url.endswith(sufixo):
                return payload
        raise AssertionError(f"URL inesperada no teste: {url}")
    return _fake


_PORTA_PROJETO = {
    "materia/166997": FIXTURE_DETALHE,
    "materia/autoria/166997": FIXTURE_AUTORIA,
    "materia/movimentacoes/166997": FIXTURE_MOVIMENTACOES,
    "materia/relatorias/166997": FIXTURE_RELATORIAS,
}


def test_obter_parlamentar_mapeia_modelo():
    async def principal():
        conector = _conector(await _fake_http_por_url({"senador/5672": FIXTURE_PARLAMENTAR}))
        return await conector.obter_parlamentar("5672")

    senador = asyncio.run(principal())
    assert isinstance(senador, ParlamentarModel)
    assert senador.id_externo == "5672"
    assert senador.nome_completo == "Alan Rick Miranda"
    assert senador.nome_urna == "Alan Rick"
    assert senador.partido == "REPUBLICANOS"
    assert senador.uf == "AC"
    assert senador.cargo == "Senador"
    assert senador.status_ativo is True
    assert senador.fonte == "senado"
    assert senador.email == "sen.alanrick@senado.leg.br"


def test_obter_projeto_normaliza_chaves_do_senado():
    async def principal():
        conector = _conector(await _fake_http_por_url(_PORTA_PROJETO))
        return await conector.obter_projeto("166997")

    projeto = asyncio.run(principal())
    assert isinstance(projeto, ProjetoDeLeiModel)
    assert projeto.id_externo == "166997"
    assert projeto.sigla_tipo == "PL"
    assert projeto.numero == 3
    assert projeto.ano == 2025
    assert projeto.ementa == "Disciplina o processo estrutural."
    assert projeto.autor_principal == "Rodrigo Pacheco"
    assert projeto.integrantes == ["Rodrigo Pacheco"]
    assert projeto.status_sn == "Sim"
    assert projeto.orgao_origem == "SENADO"
    assert projeto.data_apresentacao is not None and projeto.data_apresentacao.year == 2025
    assert projeto.pauta_tematica == "processo estrutural; jurisdição"


def test_obter_projeto_deriva_situacao_e_comissao_da_movimentacao():
    async def principal():
        conector = _conector(await _fake_http_por_url(_PORTA_PROJETO))
        return await conector.obter_projeto("166997")

    projeto = asyncio.run(principal())
    assert projeto.situacao == "Aguardando distribuição."
    assert projeto.comissao_atual == "Plenário"
    assert projeto.relator is None


def test_obter_tramitacoes_devolve_lista_ordenada():
    async def principal():
        conector = _conector(
            await _fake_http_por_url({"materia/movimentacoes/166997": FIXTURE_MOVIMENTACOES})
        )
        return await conector.obter_tramitacoes("166997")

    tramitacoes = asyncio.run(principal())
    assert isinstance(tramitacoes, list)
    assert len(tramitacoes) == 2
    assert all(isinstance(t, TramitacaoModel) for t in tramitacoes)
    primeira, segunda = tramitacoes
    assert primeira.sequencia == 1
    assert primeira.status == "Autuado o Projeto de Lei. Vai a publicação."
    assert primeira.orgao_local == "Secretaria Legislativa"
    assert segunda.sequencia == 2
    assert segunda.status == "Aguardando distribuição."
    assert segunda.orgao_local == "Plenário"


def test_obter_tramitacoes_sem_movimentos_devolve_lista_vazia():
    vazio = {"MovimentacaoMateria": {"Materia": {"Autuacoes": {"Autuacao": None}}}}

    async def principal():
        conector = _conector(await _fake_http_por_url({"materia/movimentacoes/999": vazio}))
        return await conector.obter_tramitacoes("999")

    assert asyncio.run(principal()) == []