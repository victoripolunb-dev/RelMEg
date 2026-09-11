"""Testes do Relatório Executivo em PDF (backend/servicos/exportador_pdf.py)."""
import os
from pathlib import Path

from servicos.exportador_pdf import (
    bytes_pdf_executivo,
    gerar_pdf_executivo,
    montar_relatorio_de_clipping,
)

_TEMAS_MATRIZ = [
    "ECA e proteção de crianças e adolescentes",
    "Parentalidade e licenças",
    "Prevenção e enfrentamento da violência familiar",
]


def _clipping_fake() -> dict:
    return {
        "arquivo": "Clipping_Novas_Proposicoes_2026-09-06.docx",
        "caminho": "/tmp/entregas/Clipping_Novas_Proposicoes_2026-09-06.docx",
        "periodo": "2026-09-06",
        "palavras_chave": ["família", "criança"],
        "ano": 2026,
        "total_camara": 2,
        "total_senado": 1,
        "total": 3,
        "modelo": "/tmp/entregas/MODELO A SER SEGUIDO.docx",
        "camara": [
            {"titulo": "PL 123/2026", "ementa": "Proteção de crianças", "data": "2026-09-01",
             "autor": "Deputada A", "_ft_temas": [{"tema": _TEMAS_MATRIZ[0]}]},
            {"titulo": "PL 124/2026", "ementa": "Licença parental", "data": "2026-09-02",
             "autor": "Deputado B", "_ft_temas": [{"tema": _TEMAS_MATRIZ[1]}]},
        ],
        "senado": [
            {"titulo": "PLS 5/2026", "ementa": "Violência familiar", "data": "2026-09-03",
             "autor": "Senador C", "_ft_temas": [{"tema": _TEMAS_MATRIZ[2]}]},
        ],
        "filtro": {
            "matriz": "Family Talks",
            "ativos": True,
            "temas_detectados": {tema: 1 for tema in _TEMAS_MATRIZ},
            "descartados_camara": 1,
            "descartados_senado": 0,
            "descartados_total": 1,
        },
    }


def test_montar_relatorio_de_clipping():
    relatorio = montar_relatorio_de_clipping(_clipping_fake(), cliente="Family Talks")
    assert relatorio["cliente"] == "Family Talks"
    kpis = {k["rotulo"]: k["valor"] for k in relatorio["kpis"]}
    assert kpis["Proposições relevantes"] == "3"
    assert kpis["Descartados (filtro institucional)"] == "1"
    assert len(relatorio["temas_family_talks"]) == 3
    assert relatorio["eixos_prioritarios"]
    assert len(relatorio["itens"]) == 3


def test_bytes_pdf_executivo_eh_pdf_valido():
    relatorio = montar_relatorio_de_clipping(_clipping_fake())
    conteudo = bytes_pdf_executivo(relatorio)
    assert conteudo.startswith(b"%PDF-")
    assert len(conteudo) > 1000


def test_gerar_pdf_executivo_salva_em_disco(tmp_path):
    relatorio = montar_relatorio_de_clipping(_clipping_fake())
    destino = gerar_pdf_executivo(relatorio, destino=tmp_path / "relatorio.pdf")
    assert destino.exists()
    assert destino.stat().st_size > 1000


def test_rota_pdf_executivo_payload():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as cliente:
        resposta = cliente.post(
            "/api/exportar/pdf-executivo",
            json={
                "titulo": "Relatório Executivo",
                "cliente": "Family Talks",
                "kpis": [{"rotulo": "Proposições relevantes", "valor": "12", "nota": "ok"}],
                "temas_family_talks": [
                    {"tema": "ECA e proteção de crianças e adolescentes", "total": 5}
                ],
                "eixos_prioritarios": ["ECA e proteção de crianças e adolescentes"],
                "itens": [{"titulo": "PL 1/2026", "ementa": "teste"}],
            },
        )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["arquivo"].endswith(".pdf")
    assert "caminho" not in corpo
    pasta = Path(os.environ["DIR_ENTREGAS"]) / "Relatórios"
    assert (pasta / corpo["arquivo"]).exists()


def test_rota_pdf_executivo_baixar_stream():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as cliente:
        resposta = cliente.post(
            "/api/exportar/pdf-executivo",
            json={
                "cliente": "Family Talks",
                "kpis": [{"rotulo": "Total", "valor": "5"}],
                "baixar": True,
            },
        )
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("application/pdf")
    assert resposta.content.startswith(b"%PDF-")