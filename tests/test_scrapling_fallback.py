"""
Testes do fallback por raspagem (Scrapling) no motor legislativo — B2 + B3.

Cobrem:
    - helpers ``_data_br`` e ``extrair_linha_tempo`` (heurística de timelines);
    - Câmara: API falha (HTTPError) ou vazia → ficha de tramitação raspada;
      marcas → devolve [] sem quebrar;
    - Senado: API vazia → linha do tempo da página pública.

Nenhum teste abre browser nem toca a rede: foram os conectores e o parâmetro
``_raspar`` (instância) são substituídos por fixtures simuladas.
"""
import asyncio
import httpx
from datetime import date
from unittest.mock import AsyncMock

from relmeg_core.connectors import base_connector as bc
from relmeg_core.connectors.camara import CamaraConnector
from relmeg_core.connectors.senado import SenadoConnector


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------

def test_data_br():
    assert bc._data_br("27/08/2026") == date(2026, 8, 27)
    assert bc._data_br("27/08/2026 14:22") == date(2026, 8, 27)
    assert bc._data_br("zzz") is None
    assert bc._data_br(None) is None


def test_extrair_linha_tempo_quebra_passos_por_data():
    texto = (
        "Detalhamento do andamento (PLE) ... "
        "05/09/2026 Em Plenário, primeira discussão. Despacho da Mesa. "
        "10/09/2026 Comissão de Constituição e Justiça, parecer favorável."
    )
    passos = bc.extrair_linha_tempo(texto, marcador="Detalhamento do andamento")
    assert len(passos) == 2
    assert passos[0][0] == "05/09/2026"
    assert "Plenário" in passos[0][1]
    assert "Comissão" in passos[1][1]


def test_extrair_linha_tempo_sem_datas_devolve_vazio():
    assert bc.extrair_linha_tempo("nenhuma data aqui") == []


# ---------------------------------------------------------------------------
# Câmara — fallback pela ficha de tramitação
# ---------------------------------------------------------------------------

def _conector_api_falha():
    conector = CamaraConnector()
    conector.max_tentativas = 1
    conector._http_get_json = AsyncMock(side_effect=httpx.HTTPError("API fora"))
    return conector


def test_camara_api_falha_raspa_a_ficha():
    conector = _conector_api_falha()
    conector._raspar = AsyncMock(
        return_value={
            "datas": ["27/08/2026", "01/09/2026"],
            "orgaos": ["Mesa Diretora ( MESA )", "Plenário ( PLEN )"],
            "descricoes": [
                "Apresentação do PL n. 5198/2026. Inteiro teor",
                "Discussão em Plenário.",
            ],
        }
    )
    return_value = asyncio.run(conector.obter_tramitacoes("2644723"))
    conector._raspar.assert_awaited_once()
    assert len(return_value) == 2
    primeiro = return_value[0]
    assert primeiro.data_evento == date(2026, 8, 27)
    assert primeiro.orgao_local == "Mesa Diretora ( MESA )"
    assert "Apresentação do PL" in (primeiro.descricao_fase or "")
    assert "Inteiro teor" not in (primeiro.descricao_fase or "")


def test_camara_api_vazia_tambem_usa_ficha():
    conector = CamaraConnector()
    conector.max_tentativas = 1
    conector._http_get_json = AsyncMock(return_value={"dados": []})
    conector._raspar = AsyncMock(
        return_value={
            "datas": ["27/08/2026"],
            "orgaos": ["Mesa Diretora ( MESA )"],
            "descricoes": ["Apresentação do PL n. 5198/2026."],
        }
    )
    tramitacoes = asyncio.run(conector.obter_tramitacoes("2644723"))
    conector._raspar.assert_awaited_once()
    assert len(tramitacoes) == 1


def test_camara_api_ok_nao_dispara_raspagem():
    conector = CamaraConnector()
    conector._http_get_json = AsyncMock(
        return_value={
            "dados": [
                {
                    "dataHora": "2026-08-27T12:00:00",
                    "sequencia": 1,
                    "siglaOrgao": "MESA",
                    "descricaoSituacao": "Apresentação",
                    "descricaoTramitacao": "Apresentação do PL",
                    "url": "https://www.camara.leg.br/tramitacao/1",
                }
            ]
        }
    )
    conector._raspar = AsyncMock(return_value={})
    tramitacoes = asyncio.run(conector.obter_tramitacoes("2644723"))
    conector._raspar.assert_not_awaited()
    assert len(tramitacoes) == 1
    assert tramitacoes[0].orgao_local == "MESA"


def test_camara_markup_mudado_devolve_vazio_sem_quebrar():
    conector = _conector_api_falha()
    conector._raspar = AsyncMock(return_value={"datas": [], "orgaos": [], "descricoes": []})
    assert asyncio.run(conector.obter_tramitacoes("2644723")) == []


# ---------------------------------------------------------------------------
# Senado — fallback pela página pública da matéria
# ---------------------------------------------------------------------------

def test_senado_api_vazia_fallback_na_pagina():
    conector = SenadoConnector()
    conector.max_tentativas = 1
    conector._http_get_json = AsyncMock(
        return_value={"MovimentacaoMateria": {"Materia": {"Autuacoes": {"Autuacao": []}}}}
    )
    conector._raspar = AsyncMock(
        return_value={
            "texto": (
                "Tramitação ... 01/09/2026 Recebido na Sessão do Senado. "
                "02/09/2026 Distribuído para a Comissão de Constituição e Justiça."
            )
        }
    )
    tramitacoes = asyncio.run(conector.obter_tramitacoes("166997"))
    conector._raspar.assert_awaited_once()
    assert len(tramitacoes) == 2
    assert tramitacoes[0].data_evento == date(2026, 9, 1)
    assert "Comissão" in (tramitacoes[1].descricao_fase or "")


# ---------------------------------------------------------------------------
# Comportamento base do motor
# ---------------------------------------------------------------------------

def test_usa_scrapling_class_attribute_reflete_config():
    # config padrão em produção é True; a suíte desligou via USA_SCRAPLING=false.
    conector = CamaraConnector()
    assert conector.usa_scrapling is False


def test_scrapling_instalado_nao_explode_sem_browser():
    # Deve ser determinístico: True/False, nunca exceção.
    assert isinstance(bc._scrapling_instalado(), bool)