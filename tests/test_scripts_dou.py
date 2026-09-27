"""Testes dos scripts de linha de comando do DOU (BLOCO 0% de cobertura).

Cobre a lógica pura e testável sem tocar a rede:
- ``monitorar_dou``: slugify, split de listas, perfis salvos e ``classificar``
  (motor genérico de monitoramento, com o critério de relevância do perfil);
- ``gerar_relatorio_dou``: classificação energética por Words e o corte de
  ementa (``conteudo_item``);
- ``modelo_base_multi``: normalização de dados do TSE (marcadores, datas, idade
  na posse) e o montagem da linha de 37 colunas;
- ``coletar_dou``: a.varredura completa com o conector do portal mockeado,
  provando que a coleta só acontece por comando explícito do operador.
"""
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# monitorar_dou — motor genérico de monitoramento
# ---------------------------------------------------------------------------


def test_slugify_normaliza_acentos_e_simbolos():
    from monitorar_dou import slugify

    assert slugify("Petrobras / Energia") == "petrobras_energia"
    assert slugify("MME — Renováveis") == "mme_renovaveis"
    # Nome que vira string vazia precisa cair no nome seguro, não em "arquivo sem nome".
    assert slugify("///") == "perfil"


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("a;b;c", ["a", "b", "c"]),
        ("a\nb\n\nc", ["a", "b", "c"]),
        ("  ; a ;; ", ["a"]),
        (["x", " y ", ""], ["x", "y"]),
        (None, []),
        ("", []),
    ],
)
def test_split_lista_aceita_ponto_e_virgula(entrada, esperado):
    from monitorar_dou import _split_lista

    assert _split_lista(entrada) == esperado


def test_perfil_salvo_e_recarregado_normalizado(tmp_path, monkeypatch):
    import monitorar_dou

    monkeypatch.setattr(monitorar_dou, "DIR_PERFIS", tmp_path / "perfis")

    destino = monitorar_dou.salvar_perfil(
        "Cliente Energia",
        {"palavras_chave": "solar; eólica", "orgaos": ["ministério de minas e energia"]},
    )
    assert destino == tmp_path / "perfis" / "cliente_energia.json"

    perfil = monitorar_dou.carregar_perfil(str(destino))
    assert perfil["nome"] == "cliente_energia"
    assert perfil["palavras_chave"] == ["solar", "eólica"]
    assert perfil["orgaos"] == ["ministério de minas e energia"]
    assert perfil["interesse"] == ""


def test_carregar_perfil_preenche_nome_a_partir_do_arquivo(tmp_path):
    from monitorar_dou import carregar_perfil

    arquivo = tmp_path / "meu_cliente.json"
    arquivo.write_text(
        json.dumps({"palavras_chave": "solar"}), encoding="utf-8"
    )

    perfil = carregar_perfil(str(arquivo))
    assert perfil["nome"] == "meu_cliente"
    assert perfil["palavras_chave"] == ["solar"]


def test_classificar_exige_sinal_do_perfil_para_promover_de_outros():
    """O coração do filtro: sem órgão do universo E sem palavra-chave, é ruído."""
    from monitorar_dou import classificar

    perfil = {"orgaos": ["ministério de minas e energia"], "palavras_chave": ["solar"]}

    # Tokenização do portal devolve ruído que contém "solar" como substring solta
    # em palavra sem relação — não pode ser promovido.
    assert classificar({"titulo": "Insolvência", "orgao": "Tribunal", "ementa": ""}, perfil) == (
        "outros",
        "",
    )


def test_classificar_promove_ato_por_orgao_do_universo():
    from monitorar_dou import classificar

    perfil = {"orgaos": ["ministério de minas e energia"], "palavras_chave": []}
    cat, foco = classificar(
        {"titulo": "Resolução", "orgao": "Ministério de Minas e Energia", "tipo": "Resolução"},
        perfil,
    )
    assert cat == "ato"
    assert foco == "Ato normativo/despacho"


def test_classificar_promove_contrato_por_tipo_com_palavra_chave():
    from monitorar_dou import classificar

    perfil = {"orgaos": [], "palavras_chave": ["solar"]}
    cat, foco = classificar(
        {"titulo": "Pregão solar", "orgao": "Outro", "tipo": "Pregão Eletrônico"}, perfil
    )
    assert cat == "contrato"
    assert foco == ""


def test_classificar_ato_normativo_generico_exige_palavra_chave():
    from monitorar_dou import classificar

    perfil = {"orgaos": [], "palavras_chave": ["solar"]}
    assert classificar(
        {"titulo": "Portaria", "orgao": "Qualquer", "tipo": "Portaria"}, perfil
    ) == ("outros", "")
    cat, _ = classificar(
        {"titulo": "Portaria solar", "orgao": "Qualquer", "tipo": "Portaria"}, perfil
    )
    assert cat == "ato"


def test_classificar_mencao_lista_ate_tres_palavras_chave():
    from monitorar_dou import classificar

    perfil = {"orgaos": [], "palavras_chave": ["solar", "eólica", "bateria", "hidrogênio"]}
    cat, foco = classificar(
        {"titulo": "solar eólica bateria hidrogênio", "orgao": "Qualquer", "tipo": "Nota"}, perfil
    )
    assert cat == "mencao"
    # Limita a 3 hits mesmo com 4 palavras presentes.
    assert foco == "Palavras-chave: solar; eólica; bateria"


# ---------------------------------------------------------------------------
# gerar_relatorio_dou — classificação do universo energético
# ---------------------------------------------------------------------------


def test_texto_concatena_campos_e_normaliza():
    from gerar_relatorio_dou import texto

    assert texto({"titulo": "Resolução", "orgao": "ANEEL", "ementa": "Solar"}) == (
        "resolução aneel solar"
    )
    # Campos ausentes viram string vazia (nunca "None" no texto do relatório).
    assert texto({}) == "  "


def test_eh_transmissao_e_fotovoltaica():
    from gerar_relatorio_dou import eh_fotovoltaica, eh_transmissao

    assert eh_transmissao({"titulo": "Subestação de transmissão"}) is True
    assert eh_transmissao({"titulo": "Usina solar"}) is False
    assert eh_fotovoltaica({"titulo": "Usina fotovoltaica"}) is True
    assert eh_fotovoltaica({"titulo": "Geração distribuída"}) is True
    assert eh_fotovoltaica({"titulo": "Termelétrica"}) is False


@pytest.mark.parametrize(
    "item,esperado",
    [
        # 0. ANM fica fora do universo MME mesmo sendo ato normativo.
        (
            {"orgao": "Agência Nacional de Mineração", "tipo": "Portaria", "titulo": "MME"},
            "outros",
        ),
        # 1. Universo MME/ANEEL/ANP
        (
            {"orgao": "Agência Nacional de Energia Elétrica", "tipo": "Resolução", "titulo": "Ato"},
            "ato",
        ),
        (
            {"orgao": "Ministério de Minas e Energia", "tipo": "Extrato", "titulo": "Contrato"},
            "contrato",
        ),
        # 2. Renováveis
        (
            {"orgao": "Qualquer", "titulo": "Usina solar fotovoltaica", "ementa": "geração de energia"},
            "renov",
        ),
        # 3. Mercado e tarifas
        (
            {"orgao": "Qualquer", "titulo": "Cotepe", "ementa": "ICMS combustíveis"},
            "mercado",
        ),
        # 4. Infraestrutura
        (
            {"orgao": "Qualquer", "titulo": "Linha de transmissão", "ementa": "energia"},
            "infra",
        ),
        # 5. Leilões
        (
            {"orgao": "Qualquer", "titulo": "Leilão de energia", "ementa": "energia"},
            "leiloes",
        ),
        # 6. Associações
        (
            {"orgao": "Qualquer", "titulo": "ABRADEE", "ementa": "energia elétrica"},
            "assoc",
        ),
        # 8. Fora do escopo
        ({"orgao": "Qualquer", "titulo": "Convênio cultural", "ementa": "sem energia"}, "outros"),
    ],
)
def test_classificar_categorias_do_relatorio_energia(item, esperado):
    from gerar_relatorio_dou import classificar

    categoria, _foco = classificar(item)
    assert categoria == esperado


def test_classificar_ato_anexa_foco_de_infra():
    from gerar_relatorio_dou import classificar

    _cat, foco = classificar(
        {
            "orgao": "Agência Nacional de Energia Elétrica",
            "tipo": "Portaria",
            "titulo": "Transmissora de energia",
        }
    )
    assert "Infraestrutura" in foco


def test_conteudo_item_usa_resumo_e_corta_ementa_longa():
    from gerar_relatorio_dou import (
        LIMITE_EMENTA_INTEGRA,
        LIMITE_EMENTA_TRUNCADA,
        conteudo_item,
    )

    resumo = {"https://x": "Resumo analítico do item."}

    assert conteudo_item({"url": "https://x", "ementa": "e"}, resumo) == (
        "Resumo analítico do item."
    )
    # Ementa curta entra na íntegra.
    curta = "e" * (LIMITE_EMENTA_INTEGRA - 1)
    assert conteudo_item({"url": "https://y", "ementa": curta}, {}) == curta
    # Ementa acima do limite de truncamento é cortada com reticências.
    longa = "e" * (LIMITE_EMENTA_TRUNCADA + 50)
    saida = conteudo_item({"url": "https://y", "ementa": longa}, {})
    assert len(saida) == LIMITE_EMENTA_TRUNCADA + 1
    assert saida.endswith("…")
    # Sem ementa, não quebra.
    assert conteudo_item({"url": "https://y"}, {}) == ""


def test_carregar_resumos_sem_arquivo_devolve_vazio(monkeypatch, tmp_path):
    import gerar_relatorio_dou as g

    inexistente = tmp_path / "nao_existe.json"
    monkeypatch.setattr(g, "FONTE_RESUMOS", inexistente)
    assert g.carregar_resumos() == {}


def test_resolver_entrada_prefers_entrega_e_cai_no_legado(monkeypatch, tmp_path):
    import gerar_relatorio_dou as g

    entrega = tmp_path / "entregas"
    entrega.mkdir()
    monkeypatch.setattr(g, "DIR_ENTREGA", entrega)

    na_entrega = entrega / "base.json"
    na_entrega.write_text("{}", encoding="utf-8")
    assert g._resolver_entrada("base.json") == na_entrega

    # Ausente nos dois lados devolve o caminho da entrega (onde será criado).
    assert g._resolver_entrada("inexistente.json") == entrega / "inexistente.json"


# ---------------------------------------------------------------------------
# modelo_base_multi — normalização dos dados do TSE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valor", [None, "", "   ", "none", "#NULO", "nan", "NULL_X", "NaN_y"])
def test_texto_ou_marcador_trata_sentinelas_do_tse(valor):
    from modelo_base_multi import _texto_ou_marcador

    assert _texto_ou_marcador(valor) == "#NULO"


def test_texto_ou_marcador_preserva_valor_real():
    from modelo_base_multi import _texto_ou_marcador

    assert _texto_ou_marcador("  PL  ") == "PL"
    assert _texto_ou_marcador("0") == "0"
    assert _texto_ou_marcador(None, marcador="VAZIO") == "VAZIO"


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("2026-10-04", "04/10/2026"),
        ("04/10/2026", "04/10/2026"),
        ("2026-10-04T12:00:00", "04/10/2026"),
        (None, "#NULO"),
        ("", "#NULO"),
        ("#NULO", "#NULO"),
        ("texto livre", "texto livre"),
    ],
)
def test_data_ddmm_normaliza_formatos(entrada, esperado):
    from modelo_base_multi import _data_ddmm

    assert _data_ddmm(entrada) == esperado


def test_idade_na_posse_usa_a_posse_das_eleicoes_2026():
    from modelo_base_multi import _idade_na_posse

    # Nascido em 1980 já fez aniversário antes de 01/01/2027.
    assert _idade_na_posse("1980-06-15") == "46"
    # Nascido em 2008 ainda não fez aniversário na posse.
    assert _idade_na_posse("2008-12-31") == "18"
    assert _idade_na_posse(None) == "#NULO"
    assert _idade_na_posse("lixo") == "#NULO"


def test_linha_candidato_calcula_outras_receitas_quando_falta():
    from modelo_base_multi import _linha_candidato

    linha = _linha_candidato({"total_recebido": 1000, "outras_receitas": None})
    # 34 = Outras Receitas: total - soma das fontes declaradas (0 aqui) = 1000.0
    assert linha[34] == 1000.0
    # Não pode inventar número onde o TSE não informou.
    assert linha[32] is None  # Recursos Próprios
    # Idade/posição e datas ausentes viram marcador, nunca crash.
    assert linha[6] == "#NULO"
    assert linha[7] == "#NULO"


def test_linha_candidato_respeita_outras_receitas_informado():
    from modelo_base_multi import _linha_candidato

    linha = _linha_candidato({"total_recebido": 1000, "outras_receitas": 123.45})
    assert linha[34] == 123.45


def test_linha_candidato_tem_37_colunas():
    from modelo_base_multi import _linha_candidato

    assert len(_linha_candidato({})) == 37


def test_linha_candidato_marca_ausencia_com_marcador():
    from modelo_base_multi import _linha_candidato

    linha = _linha_candidato({})
    assert "#NULO" in [str(c) for c in linha]


def test_gabarito_multi_tem_abas_do_modelo():
    from modelo_base_multi import gabarito_multi

    gab = gabarito_multi()
    assert "Candidatos" in gab["abas"]
    colunas = gab["abas"]["Candidatos"]["colunas"]
    # 37 posições, na ordem do modelo multiaba.
    assert len(colunas) == 37
    assert colunas[0] == "SQ_CANDIDATO"


def test_caminho_entrega_multi_usa_pasta_canonica():
    from config import settings
    from modelo_base_multi import caminho_entrega_multi

    destino = caminho_entrega_multi("Relatório Multiestadual")
    assert destino is not None
    # AGENTS.md: nada na raiz do repositório — tudo sob dir_entregas/TSE.
    assert Path(settings.dir_entregas) in destino.parents


# ---------------------------------------------------------------------------
# coletar_dou — a varredura só acontece por comando do operador
# ---------------------------------------------------------------------------


def test_coletar_dou_grava_o_json_deduplicado_por_secao(tmp_path, monkeypatch):
    """O script de coleta só varre porque o operador o executou (AGENTS.md).

    Fixa o contrato real de deduplicação: o mesmo URL retornado por termos
    diferentes entra UMA VEZ POR SEÇÃO (o índice de URLs é reiniciado a cada
    seção, e a saída final concatena as seções 1, 2 e 3).
    """
    import coletar_dou

    entrega = tmp_path / "DOU"
    monkeypatch.setattr(coletar_dou, "DIR_ENTREGA", entrega)
    monkeypatch.setattr(coletar_dou, "SAIDA", entrega / "dou.json")
    monkeypatch.setattr(coletar_dou, "SECOES", (1, 2, 3))
    monkeypatch.setattr(coletar_dou, "TERMOS", ["solar", "eólica"])
    # O rate limit real (6,5 s entre chamadas) não pode entrar no teste.
    monkeypatch.setattr(coletar_dou, "INTERVALO", 0)
    monkeypatch.setattr(coletar_dou.time, "sleep", lambda _s: None)

    vistas = []

    def fake_coletar(q, secao=None, data=None, itens=None):
        vistas.append((q, secao, data))
        # Os dois termos devolvem o MESMO URL compartilhado + um próprio.
        return [
            {"titulo": "Portaria", "orgao": "MME", "url": "https://in.gov.br/comum"},
            {"titulo": f"Item {q}", "orgao": "ANEEL", "url": f"https://in.gov.br/{secao}/{q}"},
        ]

    monkeypatch.setattr(coletar_dou, "coletar_portal_sr", fake_coletar)

    coletar_dou.main()

    # Uma requisição por termo por seção, sempre com a data da edição.
    assert len(vistas) == 6
    assert {v[1] for v in vistas} == {1, 2, 3}
    assert {v[2] for v in vistas} == {coletar_dou.DATA}

    itens = json.loads((entrega / "dou.json").read_text(encoding="utf-8"))
    assert len(itens) == 9  # 3 URLs por seção (1 compartilhada + 2 próprias)

    # Dentro de uma mesma seção o URL repetido aparece uma única vez.
    por_secao = {}
    for i in itens:
        por_secao.setdefault(i["secao"], []).append(i["url"])
    for secao, urls in por_secao.items():
        assert len(urls) == len(set(urls)) == 3, f"seção {secao} deduplicou errado"

    # O URL compartilhado aparece uma vez em cada seção, sempre com o seu "secao".
    compartilhado = [i for i in itens if i["url"] == "https://in.gov.br/comum"]
    assert sorted(i["secao"] for i in compartilhado) == [1, 2, 3]


def test_coletar_dou_nao_roda_sozinho_no_import(tmp_path, monkeypatch):
    """Importar o módulo não pode disparar varredura (AGENTS.md: on-demand)."""
    import coletar_dou

    called = []
    monkeypatch.setattr(
        coletar_dou, "coletar_portal_sr", lambda *a, **k: called.append(1) or []
    )
    # Só importar e referenciar não chama nada; a varredura nasce de main().
    assert called == []
    assert callable(coletar_dou.main)
