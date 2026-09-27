"""Testes do endpoint de auditoria e histórico de execuções (backend/database.py
e backend/routers/auditoria.py)."""
from fastapi.testclient import TestClient

import database
import main


def test_registrar_e_listar_eventos():
    database.registrar_evento("family_talks", "Câmara 5/8 aprovadas; Senado 3/4; 8 itens mantidos.")
    database.registrar_evento("modelo_base", "Extração TSE 2026/GO: 42 candidatos gravados.")
    database.registrar_evento("relatorio_pdf", "Relatorio_Executivo_Family-Talks_2026-09-06.pdf")
    eventos = database.listar_eventos(10)
    assert len(eventos) >= 3
    assert eventos[0]["tipo"] == "relatorio_pdf"
    assert eventos[0]["detalhe"]
    assert eventos[0]["criado_em"]


def test_resumo_metricas_tse():
    database.criar_execucao_tse("audit-1", 2026, "GO", 7)
    database.atualizar_execucao_tse(
        "audit-1", status="Concluído", total_candidatos=10,
        caminho_arquivo="C:/entregas/TSE/GO-2026.xlsx",
    )
    database.criar_execucao_tse("audit-2", 2026, "SP", 7)
    database.atualizar_execucao_tse("audit-2", status="Falhou", detalhe="403 do TSE")

    resumo = database.resumo_metricas_tse()
    assert resumo["total_execucoes"] >= 2
    assert resumo["concluidas"] >= 1
    assert resumo["falhas"] >= 1
    assert 0.0 <= resumo["taxa_sucesso"] <= 100.0
    assert resumo["total_candidatos_processados"] >= 10


def test_historico_inclui_caminho_arquivo_e_etapas():
    # Autossuficiente: cria a própria execução em vez de depender do teste
    # anterior (o conftest limpa tse_execucoes entre testes).
    database.criar_execucao_tse("audit-historico", 2026, "GO", 7)
    database.atualizar_execucao_tse(
        "audit-historico", status="Concluído", total_candidatos=7,
        caminho_arquivo="C:/entregas/TSE/GO-2026-historico.xlsx",
    )
    registros = database.historico_execucoes_tse(20)
    assert registros
    alvo = next((r for r in registros if r["task_id"] == "audit-historico"), None)
    assert alvo is not None
    assert alvo["caminho_arquivo"] == "C:/entregas/TSE/GO-2026-historico.xlsx"
    assert isinstance(alvo.get("etapas"), list)
    assert alvo["status"] in ("Concluído", "Falhou")


def test_rota_historico_e_alias():
    with TestClient(main.app) as cliente:
        resposta = cliente.get("/api/execucoes/historico?limite=5")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert "resumo_metricas" in corpo
    assert "execucoes" in corpo
    assert "eventos_recentes" in corpo

    with TestClient(main.app) as cliente:
        aliase = cliente.get("/api/execucoes/auditoria?limite=5")
    assert aliase.status_code == 200
    assert aliase.json()["resumo_metricas"] == corpo["resumo_metricas"]