"""
Testes do conector DOU (relmeg_core) — B5, sem rede.

Cobre: parsing do HTML embutido do portal (JSON no script _params), tratamento
de encoding/mojibake, normalização de hits, delegação da rota legada para o
motor, contrato do conector (busca sob demanda; coleta por item levanta
DOUSemFichaEstruturada → 501) e o registro das novas fontes (dou, almg, alesp)
no orquestrador com seus status.
"""
import pytest

from relmeg_core.connectors.dou import DOUSemFichaEstruturada, DouConnector
from relmeg_core.connectors.dou import (
    _decodificar_html,
    _extrair_objeto_params,
    _limpar_html,
    _reparar_mojibake,
    coletar_portal_sr,
    formatar_hit,
    parametros_busca,
    url_busca_oficial,
)

HIT = {
    "title": "<span class='highlight'>concessão</span> de energia",
    "hierarchyStr": "Gabinete do Ministro",
    "pubDate": "2026-08-28",
    "urlTitle": "../../../2026/08/28/edicao-123/portaria-99",
    "artType": "Portaria",
    "content": "Aprova o <b>modelo</b> de contrato de <span>concessão</span>.",
    "editionNumber": "123",
    "numberPage": "7",
}

HTML_COM_PARAMS = (
    "<html><body>"
    "<script type=\"text/javascript\" "
    "id=\"_br_com_seatecnologia_in_buscadou_BuscaDouPortlet_params\">"
    '{"jsonArray": [{"urlTitle": "x", "title": "y"}], "totalHits": 1}'
    "</script>"
    "<script>var totalPages : 3;</script>"
    "</body></html>"
)


# ---------------------------------------------------------------------------
# Parsing (server-rendered, sem browser)
# ---------------------------------------------------------------------------

def test_extrair_objeto_params_json_do_script():
    dados = _extrair_objeto_params(HTML_COM_PARAMS)
    assert dados["jsonArray"][0]["title"] == "y"
    assert dados["totalHits"] == 1


def test_extrair_objeto_params_sem_script_devolve_vazio():
    assert _extrair_objeto_params("<html><body>sem params aqui</body></html>") == {}


def test_decodificar_utf8_estrito():
    assert _decodificar_html("café".encode("utf-8")) == "café"


def test_decodificar_latin1_com_reparo_mojibake():
    # Bytes latin-1 que, lidos como utf-8, dariam mojibake (ex.: "concessÃ£o").
    bruto = "concess\u00c3\u00a3o"
    texto = _decodificar_html(bruto.encode("latin-1"))
    assert "concessão" in texto


def test_limpar_html_remove_destaques_e_tags():
    saida = _limpar_html("<span class='highlight'>concessão</span> <b>de</b> energia")
    assert saida == "concessão de energia"


def test_reparar_mojibake_sem_pista_nao_degrada():
    assert _reparar_mojibake("portaria 99/2026") == "portaria 99/2026"


# ---------------------------------------------------------------------------
# Normalização de hits e parâmetros de busca
# ---------------------------------------------------------------------------

def test_formatar_hit_normaliza_para_o_motor():
    saida = formatar_hit(HIT)
    assert saida["fonte"] == "dou"
    assert saida["id_externo"] == HIT["urlTitle"]
    assert saida["titulo"] == "concessão de energia"
    assert saida["ementa"] == "Aprova o modelo de contrato de concessão."
    assert saida["url"].startswith("https://in.gov.br/web/dou/-/")


def test_formatar_hit_sem_url_title_nao_monta_url():
    saida = formatar_hit({"title": "sem link", "pubDate": "2026-01-01"})
    assert saida["url"] is None


def test_parametros_busca_por_data_usa_dd_mm_aaaa():
    prm = parametros_busca("concessão", secao=2, data="2026-08-28")
    assert prm["s"] == "do2"
    assert prm["publishFrom"] == "28-08-2026"
    assert prm["publishTo"] == "28-08-2026"
    assert prm["exactDate"] == "personalizado"


def test_parametros_busca_por_ano_abre_janela_do_ano():
    prm = parametros_busca("concessão", ano=2026)
    assert prm["exactDate"] == "personalizado"
    assert prm["publishFrom"] == "01-01-2026"
    assert prm["publishTo"] == "31-12-2026"


def test_parametros_busca_sem_data_usa_ultimo_mes():
    prm = parametros_busca("concessão")
    assert prm["exactDate"] == "mes"


def test_url_busca_oficial_mantem_deep_link_legado():
    url = url_busca_oficial("ANEEL", secao=1, data="2026-08-28")
    assert url.startswith("https://www.in.gov.br/consulta/-/buscar/dou")
    assert "publishFrom=28-08-2026" in url
    assert "&s=do1" in url


def test_url_busca_oficial_ano_traducao_portal():
    url = url_busca_oficial("macro", ano=2026)
    assert "publishFrom=01-01-2026" in url
    assert "publishTo=31-12-2026" in url


# ---------------------------------------------------------------------------
# Contrato do conector
# ---------------------------------------------------------------------------

def test_conector_busca_delega_ao_portal_e_normaliza(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        "relmeg_core.connectors.dou.coletar_portal_sr",
        lambda *a, **k: [formatar_hit(HIT)],
    )
    conector = DouConnector()
    resultados = asyncio.run(
        conector.buscar_proposicoes("concessão", ano=2026, itens=5)
    )
    assert len(resultados) == 1
    assert resultados[0]["fonte"] == "dou"
    assert resultados[0]["titulo"] == "concessão de energia"


def test_conector_busca_valida_secao_e_data():
    import asyncio

    conector = DouConnector()
    with pytest.raises(ValueError):
        asyncio.run(conector.buscar_proposicoes("x", secao=9))
    with pytest.raises(ValueError):
        asyncio.run(conector.buscar_proposicoes("x", data="28/08/2026"))


def test_conector_usa_scrapling_desligado_e_status():
    assert DouConnector.usa_scrapling is False
    assert DouConnector.status_v1 == "pronta (busca)"


def test_obter_projeto_levanta_501_mapeavel():
    import asyncio

    conector = DouConnector()
    with pytest.raises(DOUSemFichaEstruturada):
        asyncio.run(conector.obter_projeto("qualquer-id"))


def test_obter_tramitacoes_levanta_501_mapeavel():
    import asyncio

    conector = DouConnector()
    with pytest.raises(DOUSemFichaEstruturada):
        asyncio.run(conector.obter_tramitacoes("qualquer-id"))


# ---------------------------------------------------------------------------
# Registro e status (GUI /hub/fontes)
# ---------------------------------------------------------------------------

def test_fontes_novas_registradas_no_orquestrador():
    from relmeg_core.orquestrador import OrquestradorLegislativo

    status = OrquestradorLegislativo().fontes_disponiveis()
    assert status["dou"] == "pronta (busca)"
    assert "API" in status["almg"] and "v2" in status["almg"]
    assert "CSV" in status["alesp"]
    assert "indisponível" in status["algo"]


def test_fonte_dou_no_hub_devolve_501_para_coleta_por_item(monkeypatch):
    import sys
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware

    raiz = Path(__file__).resolve().parents[1] / "backend"
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))

    from rate_limit import limiter
    from routers import hub

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.include_router(hub.router)

    with TestClient(app) as client:
        resposta = client.post("/hub/proposicoes/dou/qualquer-id")
        assert resposta.status_code == 501
        assert "dou" in resposta.json()["detail"].lower()