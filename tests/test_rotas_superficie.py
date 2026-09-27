"""Testes das rotas de superfície (/dou, /ai) e dos validadores do TSE.

Cobre duas frentes que estavam sem exercício: o tratamento de erro da integração
do portal do DOU (503), o resumo determinístico do /ai/resumir-dou e as validações
de entrada das rotas do TSE (UF, ano eleitoral, cargo e código de município).

Nenhum teste toca a rede: o conector do portal e o DB são sempre substituídos.
"""
import pytest


@pytest.fixture
def cliente():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:
        yield c


# ---------------------------------------------------------------------------
# /dou/pesquisa
# ---------------------------------------------------------------------------


def test_dou_pesquisa_devolve_resultados_sem_campos_internos(cliente, monkeypatch):
    import routers.dou as dou

    hits = [
        {
            "titulo": "Portaria ANEEL",
            "orgao": "ANEEL",
            "url": "https://in.gov.br/1",
            "fonte": "dou",
            "id_externo": "999",
        }
    ]
    monkeypatch.setattr(dou, "coletar_portal_sr", lambda *a, **k: hits)

    r = cliente.get("/dou/pesquisa", params={"q": "ANEEL", "data": "2026-09-15"})

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["total"] == 1
    assert corpo["termo_pesquisado"] == "ANEEL"
    assert corpo["data_filtro"] == "2026-09-15"
    # Campos internos de normalização do hub não vazam para o contrato público.
    assert "fonte" not in corpo["resultados"][0]
    assert "id_externo" not in corpo["resultados"][0]
    assert corpo["url_busca_oficial"].startswith("https://")


def test_dou_pesquisa_vazio_mantem_contrato_e_data_padrao(cliente, monkeypatch):
    import routers.dou as dou

    monkeypatch.setattr(dou, "coletar_portal_sr", lambda *a, **k: [])

    r = cliente.get("/dou/pesquisa", params={"q": "termoxinexistente"})

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["total"] == 0
    assert corpo["resultados"] == []
    # Sem data no filtro, a resposta diz isso explicitamente em vez de devolver None.
    assert corpo["data_filtro"] == "Mais recentes / Sem data fixa"


def test_dou_pesquisa_falha_do_portal_vira_503(cliente, monkeypatch):
    import routers.dou as dou

    def explode(*_a, **_k):
        raise RuntimeError("portal fora do ar")

    monkeypatch.setattr(dou, "coletar_portal_sr", explode)

    r = cliente.get("/dou/pesquisa", params={"q": "ANEEL"})

    assert r.status_code == 503
    # A mensagem é para o operador, sem vazar stack/URL interna.
    assert "instável" in r.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [
        {"q": "ab"},                      # termo curto demais
        {"q": "a" * 151},                 # termo longo demais
        {"q": "ANEEL", "data": "15-09-2026"},   # data fora do formato
        {"q": "ANEEL", "secao": 0},            # seção abaixo de 1
        {"q": "ANEEL", "secao": 4},            # seção acima de 3
        {"q": "ANEEL", "itens": 0},            # itens abaixo de 1
    ],
)
def test_dou_pesquisa_rejeita_parametros_invalidos(cliente, params):
    assert cliente.get("/dou/pesquisa", params=params).status_code == 422


# ---------------------------------------------------------------------------
# /ai/resumir-dou
# ---------------------------------------------------------------------------


def test_ai_resumir_dou_usa_titulo_e_conta_palavras(cliente):
    r = cliente.post(
        "/ai/resumir-dou",
        json={"titulo": "  Portaria ANEEL 123  ", "texto": "um dois tres quatro cinco"},
    )

    assert r.status_code == 200
    corpo = r.json()
    # Título é normalizado (trim) e o resumo cita a contagem real de palavras.
    assert corpo["titulo"] == "Portaria ANEEL 123"
    assert "Portaria ANEEL 123" in corpo["resumo"]
    assert "5 palavras" in corpo["resumo"]


def test_ai_resumir_dou_sem_titulo_usa_texto_padrao(cliente):
    r = cliente.post("/ai/resumir-dou", json={"texto": "conteudo do ato"})

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["titulo"] is None
    assert "publicação oficial no Diário Oficial da União" in corpo["resumo"]


@pytest.mark.parametrize(
    "payload",
    [
        {"texto": ""},        # min_length=1
        {"texto": "a" * 200_001},  # max_length
        {},                   # campo obrigatório ausente
    ],
)
def test_ai_resumir_dou_rejeita_payload_invalido(cliente, payload):
    assert cliente.post("/ai/resumir-dou", json=payload).status_code == 422


# ---------------------------------------------------------------------------
# Validadores do TSE (usados por todas as rotas /tse)
# ---------------------------------------------------------------------------


def test_id_eleicao_mapeia_ano_para_id_oficial():
    from routers.tse import _id_eleicao

    assert _id_eleicao(2026) == "2055502026"
    assert _id_eleicao(2024) == "2045202024"


def test_id_eleicao_ano_desconhecido_vira_400_com_anos_validos():
    from fastapi import HTTPException

    from routers.tse import _id_eleicao

    with pytest.raises(HTTPException) as erro:
        _id_eleicao(2019)
    assert erro.value.status_code == 400
    # A mensagem diz quais anos o operador pode usar.
    assert "2026" in erro.value.detail


@pytest.mark.parametrize(
    "ano,municipal",
    [(2024, True), (2020, True), (2026, False), (2022, False), (2018, False)],
)
def test_eh_municipal_por_ano(ano, municipal):
    from routers.tse import _eh_municipal

    assert _eh_municipal(ano) is municipal


@pytest.mark.parametrize(
    "uf,liberar_br,valido",
    [
        ("GO", False, True),
        ("go", False, True),      # minúscula é normalizada
        (" BR ", True, True),     # BR com espaços é aparado
        ("BR", False, True),      # BR é aceito no formato (2 letras)
        ("G", False, False),      # tamanho errado
        ("12", False, False),     # não-alfabético
        ("", False, False),
    ],
)
def test_validar_uf_confere_apenas_o_formato(uf, liberar_br, valido):
    """A validação de UF é de FORMATO (2 letras), não de existência na tabela.

    Sigla inexistente como "XX" passa pelo validador e é recusada pela API do TSE
    — o teste fixa esse contrato para que ninguém confie na validação local.
    """
    from fastapi import HTTPException

    from routers.tse import _validar_uf

    if valido:
        assert _validar_uf(uf, liberar_br=liberar_br) == uf.strip().upper()
    else:
        with pytest.raises(HTTPException) as erro:
            _validar_uf(uf, liberar_br=liberar_br)
        assert erro.value.status_code == 400


@pytest.mark.parametrize(
    "codigo,municipal,valido",
    [
        (13, True, True),    # Vereador em eleição municipal
        (11, True, True),    # Prefeito
        (7, True, False),    # Deputado Estadual não é cargo municipal
        (7, False, True),    # ... mas é cargo geral
        (99, False, False),
    ],
)
def test_validar_cargo_usa_a_tabela_do_ano(codigo, municipal, valido):
    from fastapi import HTTPException

    from routers.tse import _validar_cargo

    if valido:
        assert _validar_cargo(codigo, municipal) == codigo
    else:
        with pytest.raises(HTTPException) as erro:
            _validar_cargo(codigo, municipal)
        assert erro.value.status_code == 400
        # A mensagem diz se é tabela municipal ou geral.
        assert ("municipal" if municipal else "geral") in erro.value.detail


def test_tse_candidatos_rejeita_municipario_nao_numerico(cliente):
    """A rota rejeita lixo em `municipio` ANTES de qualquer chamada externa."""
    base = {"ano": 2026, "uf": "GO", "codigo_cargo": 7}

    for ruim in ["abc", "..%2Fetc", "x%3Fy%3D1", "7a", "%20"]:
        r = cliente.get("/tse/candidatos", params={**base, "municipio": ruim})
        assert r.status_code == 422, f"municipio={ruim!r} deveria ser 422"


def test_tse_candidatos_rejeita_cargo_fora_da_tabela_do_ano(cliente):
    """Cargo de eleição geral em ano municipal é recusado com 400, sem rede."""
    r = cliente.get(
        "/tse/candidatos",
        params={"ano": 2024, "uf": "GO", "codigo_cargo": 7},  # estadual ≠ municipal
    )
    assert r.status_code == 400
    assert "municipal" in r.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [
        {"ano": 2026, "uf": "GO", "codigo_cargo": 99},   # cargo acima de 13
        {"ano": 1990, "uf": "GO", "codigo_cargo": 7},    # ano fora de 2018..2100
        {"ano": 2019, "uf": "GO", "codigo_cargo": 7},    # ano sem id_eleicao
        {"ano": 2026, "uf": "G", "codigo_cargo": 7},     # UF com 1 letra
    ],
)
def test_tse_candidatos_rejeita_parametros_invalidos(cliente, params):
    assert cliente.get("/tse/candidatos", params=params).status_code in (400, 422)
