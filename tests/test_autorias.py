"""Testes da autoria formal parlamentar <-> proposição (B4).

Cobre a tabela relmeg_autorias, o preenchimento a partir de integrantes do
ProjetoDeLeiModel e a resolução por nome ao salvar o parlamentar.
"""
from datetime import date

import pytest

import database as db
from relmeg_core.models.schemas import ParlamentarModel, ProjetoDeLeiModel


def _projeto(**kwargs):
    base = dict(
        fonte="camara",
        id_externo="1234",
        sigla_tipo="PL",
        numero=1234,
        ano=2026,
        ementa="Ementa de teste de autoria.",
        orgao_origem="camara",
        autor_principal="Dep. José da Conceição",
        integrantes=["Dep. José da Conceição", "Dep. Maria de Lourdes"],
        data_apresentacao=date(2026, 1, 10),
    )
    base.update(kwargs)
    return ProjetoDeLeiModel(**base)


def _parlamentar(**kwargs):
    base = dict(
        fonte="camara",
        id_externo="111",
        nome_completo="José da Conceição",
        uf="DF",
        partido="PSD",
        nome_urna="José Conceição",
        cargo="Deputado Federal",
    )
    base.update(kwargs)
    return ParlamentarModel(**base)


def test_salvar_projeto_popula_autorias_e_ordena_principal():
    db.salvar_projeto(_projeto())
    autorias = db.listar_autorias("camara", "1234")
    assert len(autorias) == 2
    nomes_ord = [a["autor_nome"] for a in autorias]
    assert nomes_ord[0] == "Dep. José da Conceição"
    assert autorias[0]["principal"] == 1
    assert autorias[1]["principal"] == 0
    assert autorias[0]["autor_id_externo"] is None


def test_resolucao_por_nome_normalizado_ignora_prefixo_e_acentos():
    db.salvar_projeto(_projeto())
    db.salvar_parlamentar(_parlamentar())
    autorias = db.listar_autorias("camara", "1234")
    by_name = {a["autor_nome"]: a for a in autorias}
    assert by_name["Dep. José da Conceição"]["autor_id_externo"] == "111"
    assert by_name["Dep. Maria de Lourdes"]["autor_id_externo"] is None

    constelacoes = db.listar_autorias_de_parlamentar("camara", "111")
    assert len(constelacoes) == 1
    assert constelacoes[0]["id_externo"] == "1234"
    assert constelacoes[0]["principal"] == 1


def test_reescrever_projeto_substitui_autores_sem_sobras():
    db.salvar_projeto(_projeto())
    db.salvar_projeto(_projeto(integrantes=["Dep. João Pereira"]))
    autorias = db.listar_autorias("camara", "1234")
    assert [a["autor_nome"] for a in autorias] == ["Dep. João Pereira"]
    assert autorias[0]["principal"] == 1


def test_sem_integrantes_usa_autor_principal_isso():
    db.salvar_projeto(_projeto(integrantes=[]))
    autorias = db.listar_autorias("camara", "1234")
    assert len(autorias) == 1
    assert autorias[0]["autor_nome"] == "Dep. José da Conceição"


def test_resolucao_por_nome_de_urna():
    db.salvar_projeto(_projeto(integrantes=["Zé da Conceição"]))
    db.salvar_parlamentar(_parlamentar(id_externo="222", nome_urna="Zé da Conceição"))
    autorias = db.listar_autorias("camara", "1234")
    assert autorias[0]["autor_id_externo"] == "222"


def test_autoria_de_outra_fonte_nao_resolve():
    db.salvar_projeto(_projeto())
    db.salvar_parlamentar(_parlamentar(fonte="senado", id_externo="333"))
    autorias = db.listar_autorias("camara", "1234")
    assert autorias[0]["autor_id_externo"] is None