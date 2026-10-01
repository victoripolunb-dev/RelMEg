"""Testes das guardas de arquitetura (scripts/verificar_contrato.py).

Uma guarda de CI que nunca falha não protege nada. Estes testes criam árvores
temporárias com as infrações e provam que cada verificação REJEITA, e que uma
árvore limpa passa. Sem isto, o CI só confirmaria que o script roda, não que
ele caça.
"""
import importlib.util
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parent.parent
_SCRIPT = _RAIZ / "scripts" / "verificar_contrato.py"

spec = importlib.util.spec_from_file_location("verificar_contrato", _SCRIPT)
contrato = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contrato)


def _arvore(tmp_path: Path, arquivos: dict) -> Path:
    for nome, conteudo in arquivos.items():
        destino = tmp_path / nome
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(conteudo, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# BackgroundTask
# ---------------------------------------------------------------------------


def test_backgroundtask_fora_da_excecao_e_reprovado(tmp_path):
    raiz = _arvore(tmp_path, {"backend/routers/hub.py": "from fastapi import BackgroundTasks\n"})
    assert contrato.verifica_background(raiz)


def test_backgroundtask_na_excecao_autorizada_e_aceito(tmp_path):
    raiz = _arvore(tmp_path, {"backend/servicos/extrator_tse.py": "tasks = BackgroundTasks()\n"})
    assert contrato.verifica_background(raiz) == []


def test_backgroundtask_em_comentario_nao_conta(tmp_path):
    raiz = _arvore(
        tmp_path,
        {"backend/database.py": "# ver comentário sobre BackgroundTasks\n"},
    )
    assert contrato.verifica_background(raiz) == []


# ---------------------------------------------------------------------------
# crons
# ---------------------------------------------------------------------------


def test_vercel_com_crons_e_reprovado(tmp_path):
    raiz = _arvore(tmp_path, {"vercel.json": '{"crons": [{"path": "/tse/x"}]}'})
    assert contrato.verifica_crons(raiz)


def test_vercel_sem_crons_e_aceito(tmp_path):
    raiz = _arvore(tmp_path, {"vercel.json": '{"functions": {}}'})
    assert contrato.verifica_crons(raiz) == []


# ---------------------------------------------------------------------------
# polling / laço infinito
# ---------------------------------------------------------------------------


def test_while_true_sem_saida_e_reprovado(tmp_path):
    """Laço infinito é a assinatura de um dispatcher automático."""
    raiz = _arvore(
        tmp_path,
        {
            "backend/worker.py": (
                "def roda():\n"
                "    while True:\n"
                "        coletar()\n"
            )
        },
    )
    problemas = contrato.verifica_polling(raiz)
    assert problemas
    assert "sem break/return/raise" in problemas[0]


@pytest.mark.parametrize(
    "corpo",
    [
        "    while True:\n        coletar()\n        break\n",           # break
        "    while True:\n        return coletar()\n",                   # return
        "    while True:\n        raise Erro()\n",                      # raise
    ],
)
def test_while_true_delimitado_e_aceito(tmp_path, corpo):
    """Retry com teto e paginação são lícitos dentro de um handler."""
    raiz = _arvore(tmp_path, {"backend/ok.py": "def f():\n" + corpo})
    assert contrato.verifica_polling(raiz) == []


def test_while_true_com_saida_condicionada_e_aceito(tmp_path):
    """Laço que retorna em dentro do corpo é delimitado e aceito."""
    raiz = _arvore(
        tmp_path,
        {
            "backend/ok.py": (
                "def f():\n"
                "    while True:\n"
                "        x = coletar()\n"
                "        if x:\n"
                "            return x\n"
            )
        },
    )
    assert contrato.verifica_polling(raiz) == []


def test_while_true_que_sai_so_apos_o_laco_e_reprovado(tmp_path):
    """Um `return` DEPOIS do laço não o fecha: continua sendo laço infinito."""
    raiz = _arvore(
        tmp_path,
        {
            "backend/worker.py": (
                "def f():\n"
                "    while True:\n"
                "        try:\n"
                "            coletar()\n"
                "        except Erro:\n"
                "            pass\n"
                "    return 1\n"
            )
        },
    )
    problemas = contrato.verifica_polling(raiz)
    assert problemas
    assert "sem break/return/raise" in problemas[0]


def test_setinterval_no_frontend_e_reprovado(tmp_path):
    raiz = _arvore(tmp_path, {"src/app.js": "setInterval(() => carregar(), 5000);\n"})
    assert contrato.verifica_polling(raiz)


# ---------------------------------------------------------------------------
# entregas
# ---------------------------------------------------------------------------


def test_entrega_na_raiz_do_repo_e_reprovada(tmp_path):
    raiz = _arvore(tmp_path, {"Relatorio_cliente.docx": "x"})
    assert contrato.verifica_entregas(raiz)


@pytest.mark.parametrize(
    "caminho",
    [
        "RelMeg - Entregas/Relatorios/TSE/relatorio.docx",
        "backend/templates/MODELO A SER SEGUIDO.docx",
        "Modelo base/Modelo base de coleta - Parlamentares.xlsx",
        "TUTORIAL.docx",
    ],
)
def test_entregas_em_lugar_permitido_sao_aceitas(tmp_path, caminho):
    raiz = _arvore(tmp_path, {caminho: "x"})
    assert contrato.verifica_entregas(raiz) == []


def test_venv_nao_e_vasculhada(tmp_path):
    """Nem .venv nem node_modules podem disparar violação."""
    raiz = _arvore(
        tmp_path,
        {
            ".venv/Lib/site-packages/pacote/worker.py": "while True:\n    coletar()\n",
            "node_modules/pacote/index.js": "setInterval(poll, 1000);\n",
        },
    )
    assert contrato.verifica_polling(raiz) == []


# ---------------------------------------------------------------------------
# a árvore real do repositório precisa passar
# ---------------------------------------------------------------------------


def test_repositorio_real_respeita_todas_as_guardas():
    """A prova de que as guardas acima não são decorativas: o repo passa."""
    assert contrato.main(["verificar_contrato", str(_RAIZ)]) == 0
