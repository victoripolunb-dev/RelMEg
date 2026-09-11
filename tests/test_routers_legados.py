"""Testes dos routers legados reescritos para async httpx (sem rede).

Cobre deputados, autores, frentes, eventos, monitoramento e comissões do
Senado — antes sem nenhuma cobertura. Usa um ``httpx.AsyncClient`` fake para
garantir zero chamadas externas.
"""
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

_RAIZ = Path(__file__).resolve().parent.parent
_BACKEND = _RAIZ / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from rate_limit import limiter
from routers import (
    autores,
    deputados,
    eventos,
    frentes,
    monitoramento,
    proposicoes,
)
from routers.senado import comissoes as senado_comissoes
from routers.senado import materias as senado_materias


# ---------------------------------------------------------------------------
# httpx fake (context manager assíncrono) — mapeia sufixos de URL para payloads
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.ConnectError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class _FakeAsyncClient:
    """Substitui httpx.AsyncClient: devolve payloads conforme o sufixo da URL."""

    def __init__(self, mapa):
        self.mapa = mapa
        self.padrao = _FakeResponse(
            {"dados": [], "erro": "sem rota simulada"}, status_code=404
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, params=None, headers=None, **kwargs):
        for sufixo, payload in self.mapa.items():
            if url.endswith(sufixo):
                if isinstance(payload, _FakeResponse):
                    return payload
                return _FakeResponse(payload)
        return self.padrao


@pytest.fixture()
def client(monkeypatch):
    """App mínimo com os routers testados + rate limit real do slowapi."""
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    for rota in (
        deputados.router,
        autores.router,
        frentes.router,
        eventos.router,
        monitoramento.router,
        senado_comissoes.router,
        proposicoes.router,
        senado_materias.router,
    ):
        app.include_router(rota)

    def _substituir(mapa):
        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _FakeAsyncClient(mapa))

    _substituir({})
    client_fixture = TestClient(app)
    client_fixture._relmeg_substituir = _substituir
    with client_fixture as c:
        yield c


# ---------------------------------------------------------------------------
# Deputados
# ---------------------------------------------------------------------------

_FIXTURE_DEPUTADOS = {
    "dados": [
        {"nome": "Abílio Santana", "siglaPartido": "PL", "siglaUf": "BA"},
        {"nome": "Ana Souza", "siglaPartido": "PSDB", "siglaUf": "SP"},
    ]
}


def test_deputados_retorna_lista_formatada(client):
    client._relmeg_substituir({"api/v2/deputados": _FIXTURE_DEPUTADOS})
    resposta = client.get("/deputados/?itens=5")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 2
    assert corpo["deputados"][0]["nome"] == "Abílio Santana"
    assert corpo["deputados"][0]["partido"] == "PL"
    assert corpo["deputados"][0]["uf"] == "BA"


def test_deputados_erro_fonte_retorna_502(client):
    client._relmeg_substituir({"api/v2/deputados": _FakeResponse({"erro": "boom"}, 503)})
    resposta = client.get("/deputados/")
    assert resposta.status_code == 502


def test_deputados_parametro_invalido_422(client):
    resposta = client.get("/deputados/?itens=0")
    assert resposta.status_code == 422


# ---------------------------------------------------------------------------
# Autores (prefixo /autores, corrigido da colisão com /proposicoes)
# ---------------------------------------------------------------------------

_FIXTURE_AUTORES = {
    "dados": [
        {"nome": "Dep. Ana Souza", "tipo": "Deputado", "siglaPartido": "PSDB", "siglaUf": "SP"},
    ]
}


def test_autores_retorna_lista(client):
    client._relmeg_substituir({"proposicoes/123/autores": _FIXTURE_AUTORES})
    resposta = client.get("/autores/123/autores")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["proposicao_id"] == 123
    assert corpo["total_autores"] == 1
    assert corpo["autores"][0]["partido"] == "PSDB"


def test_autores_rota_no_prefixo_autores_nao_colide(client):
    resposta = client.get("/proposicoes/123/autores")
    assert resposta.status_code == 404  # route só existe em /autores


# ---------------------------------------------------------------------------
# Frentes
# ---------------------------------------------------------------------------

_FIXTURE_FRENTES = {
    "dados": [
        {"id": 1, "titulo": "Frente da Família", "idLegislatura": 57},
    ]
}

_FIXTURE_MEMBROS = {
    "dados": [
        {"nome": "Ana Souza", "titulo": "Presidente", "siglaPartido": "PSDB", "siglaUf": "SP"},
    ]
}


def test_frentes_lista(client):
    client._relmeg_substituir({"api/v2/frentes": _FIXTURE_FRENTES})
    resposta = client.get("/frentes/")
    assert resposta.status_code == 200
    assert resposta.json()["total"] == 1


def test_frentes_membros(client):
    client._relmeg_substituir({"frentes/1/membros": _FIXTURE_MEMBROS})
    resposta = client.get("/frentes/1/membros")
    assert resposta.status_code == 200
    assert resposta.json()["membros"][0]["nome"] == "Ana Souza"


def test_frentes_membros_falha_502(client):
    client._relmeg_substituir({"frentes/1/membros": _FakeResponse({"erro": "boom"}, 500)})
    resposta = client.get("/frentes/1/membros")
    assert resposta.status_code == 502


# ---------------------------------------------------------------------------
# Eventos
# ---------------------------------------------------------------------------

_FIXTURE_EVENTOS = {
    "dados": [
        {
            "id": 1,
            "dataHoraInicio": "2026-09-15T10:00:00",
            "descricao": "Audiência sobre o Estatuto da Família",
            "descricaoTipo": "Audiência Pública",
            "localSala": "Plenário 3",
        },
    ]
}


def test_eventos_lista(client):
    client._relmeg_substituir({"api/v2/eventos": _FIXTURE_EVENTOS})
    resposta = client.get("/eventos/")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["eventos"][0]["local"] == "Plenário 3"


def test_eventos_data_invalida_422(client):
    resposta = client.get("/eventos/?dataInicio=15/09/2026")
    assert resposta.status_code == 422


# ---------------------------------------------------------------------------
# Monitoramento (Câmara + Senado)
# ---------------------------------------------------------------------------

_FIXTURE_MONITORIA_CAMARA = {
    "dados": [
        {
            "id": 100,
            "uri": "https://dadosabertos.camara.leg.br/api/v2/proposicoes/100",
            "siglaTipo": "PL",
            "numero": 900,
            "ano": 2026,
            "ementa": "Dispõe sobre o incentivo à primeira infância.",
        },
    ]
}

_FIXTURE_MONITORIA_SENADO = {
    "PesquisaBasicaMateria": {
        "Materias": {
            "Materia": [
                {
                    "Codigo": 500,
                    "Sigla": "PL",
                    "DescricaoIdentificacao": "PL 123/2026",
                    "Numero": 123,
                    "Ano": 2026,
                    "Ementa": "Cria o programa de combate à violência doméstica.",
                    "Autor": "Sen. Maria Souza",
                    "Data": "2026-09-01",
                    "UrlDetalheMateria": "https://www25.senado.leg.br/materia/500",
                },
                {
                    "Codigo": 501,
                    "Sigla": "PL",
                    "Numero": 124,
                    "Ano": 2026,
                    "Ementa": "Trata de tributos municipais.",
                    "Autor": "Sen. João Lima",
                },
            ]
        }
    }
}


def test_monitoramento_camara_filtra(client):
    client._relmeg_substituir({"api/v2/proposicoes": _FIXTURE_MONITORIA_CAMARA})
    resposta = client.get("/monitoramento/camara?q=primeira+infancia")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["termo_pesquisado"] == "primeira infancia"
    assert corpo["total"] == 1
    assert corpo["resultados"][0]["ementa"].startswith("Dispõe")


def test_monitoramento_senado_filtra_por_ementa(client):
    client._relmeg_substituir({"pesquisa/lista": _FIXTURE_MONITORIA_SENADO})
    resposta = client.get("/monitoramento/senado?q=violencia+domestica&ano=2026")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["resultados"][0]["codigo"] == 500
    assert corpo["resultados"][0]["sigla"] == "PL"


def test_monitoramento_senado_ignora_dict_em_vez_de_lista(client):
    fixture_single = {"PesquisaBasicaMateria": {"Materias": {"Materia":
        {"Codigo": 42, "Sigla": "REQ", "Ementa": "Requerimento de prioridade."}
    }}}
    client._relmeg_substituir({"pesquisa/lista": fixture_single})
    resposta = client.get("/monitoramento/senado?q=prioridade")
    assert resposta.status_code == 200
    assert resposta.json()["total"] == 1


# ---------------------------------------------------------------------------
# Comissões do Senado
# ---------------------------------------------------------------------------

_FIXTURE_COMISSOES = {
    "ListaComissoes": {
        "Comissoes": {
            "Comissao": [
                {"CodigoComissao": 1, "SiglaComissao": "CE", "NomeComissao": "Comissão de Educação", "CasaComissao": "SF"},
            ]
        }
    }
}


def test_comissoes_senado(client):
    client._relmeg_substituir({"dadosabertos/comissoes": _FIXTURE_COMISSOES})
    resposta = client.get("/senado/comissoes/")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["total"] == 1
    assert corpo["comissoes"][0]["sigla"] == "CE"


# ---------------------------------------------------------------------------
# Proposições da Câmara (rota enriquecida) — 422/502 sem quebrar shape
# ---------------------------------------------------------------------------

def test_proposicoes_retorna_502_quando_fonte_falha(client):
    client._relmeg_substituir({"api/v2/proposicoes": _FakeResponse({"erro": "boom"}, 500)})
    resposta = client.get("/proposicoes/?siglaTipo=PL")
    assert resposta.status_code == 502


def test_proposicoes_termo_curto_422(client):
    resposta = client.get("/proposicoes/?keywords=ab")
    assert resposta.status_code == 422