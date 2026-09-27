"""Guardas de arquitetura do RelMeg — invocado no CI e auditável localmente.

O AGENTS.md define uma regra não negociável: TODO consumo de API
governamental é estritamente sob demanda do operador. Configuração não é
documentação — estas guardas existem porque uma infração silenciosa custa
rate limit institucional e não aparece em nenhum teste funcional.

Verificações (todas com saída explicativa e código de erro != 0 na falha):

1. ``BackgroundTask``/``BackgroundTasks``/``add_task(`` só podem aparecer nos
   arquivos da exceção autorizada e, nos demais, apenas em comentário.
2. Nenhum ``vercel.json`` pode declarar a chave ``"crons"``.
3. Nenhum ``setInterval`` (polling de frontend) nem ``while True`` sem
   ``break``/``return``/``raise`` — um laço infinito é a assinatura de um
   dispatcher automático.
4. Nenhum artefato de entrega (.docx/.pdf/.xlsx) na raiz do repositório.

Uso:
    python scripts/verificar_contrato.py [raiz_do_repo]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Arquivos onde BackgroundTasks é lícito (a exceção do AGENTS.md: apenas a
# extração pesada do TSE, disparada por requisição explícita do operador).
ARQUIVOS_COM_BACKGROUND = {
    "backend/servicos/extrator_tse.py",
    "backend/routers/tse.py",
    "backend/database.py",
}

IGNORAR_DIRETORIOS = {
    ".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache",
    "site-packages", ".ruff_cache", "htmlcov", "coverage",
}

# Contratos de operador e documentação que ficam versionados de propósito.
#   "backend/templates"  -> o MODELO A SER SEGUIDO.docx é contrato do exportador
#   "Modelo base"        -> planilha que o operador preenche e versiona
#   "TUTORIAL.docx"      -> documentação do projeto, não é entrega a cliente
#   qualquer pasta "*Entregas*" -> destino canônico das entregas
PARTES_PERMITIDAS = ("backend/templates", "Modelo base")
ARQUIVOS_PERMITIDOS = ("TUTORIAL.docx",)

# O próprio verificador e os testes de contrato mencionam os padrões que caçam
# (é assim que se prova que a guarda funciona); sem esta exceção eles acusariam
# o próprio código.
IGNORAR_ARQUIVOS = {"scripts/verificar_contrato.py"}
# A suíte de testes precisa nomear as construções proibidas para poder rejeitá-las.
IGNORAR_PREFIXOS = ("tests/",)

_PADRAO_BACKGROUND = re.compile(r"\bBackgroundTasks?\b|\badd_task\s*\(")
_PADRAO_CRONS = re.compile(r'"crons"\s*:')
_PADRAO_SETINTERVAL = re.compile(r"\bsetInterval\s*\(")
_PADRAO_WHILE_TRUE = re.compile(r"^\s*while\s+True\s*:")
_PADRAO_SAIDA = re.compile(r"^\s*(break|return\b|raise\b)")
_COMENTARIO = re.compile(r"^\s*#")

EXTENSAO_ENTREGA = {".docx", ".pdf", ".xlsx"}


def _arquivos(raiz: Path, sufixos: set[str] | None = None):
    for caminho in raiz.rglob("*"):
        if not caminho.is_file():
            continue
        if any(parte in IGNORAR_DIRETORIOS for parte in caminho.parts):
            continue
        if sufixos and caminho.suffix not in sufixos:
            continue
        yield caminho


def _relativo(caminho: Path, raiz: Path) -> str:
    return caminho.relative_to(raiz).as_posix()


def _ignorado(rel: str) -> bool:
    return rel in IGNORAR_ARQUIVOS or rel.startswith(IGNORAR_PREFIXOS)


def verifica_background(raiz: Path) -> list[str]:
    """BackgroundTask fora da exceção autorizada (ignorando comentários)."""
    problemas = []
    for caminho in _arquivos(raiz, {".py"}):
        rel = _relativo(caminho, raiz)
        if rel in ARQUIVOS_COM_BACKGROUND or _ignorado(rel):
            continue
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _COMENTARIO.match(linha):
                continue
            if _PADRAO_BACKGROUND.search(linha):
                problemas.append(
                    f"{rel}:{numero}: BackgroundTask fora da exceção autorizada: {linha.strip()}"
                )
    return problemas


def verifica_crons(raiz: Path) -> list[str]:
    """Nenhum vercel.json com a chave proibida "crons"."""
    problemas = []
    for caminho in _arquivos(raiz, {".json"}):
        if caminho.name != "vercel.json":
            continue
        if _PADRAO_CRONS.search(caminho.read_text(encoding="utf-8", errors="replace")):
            problemas.append(f"{_relativo(caminho, raiz)}: declara a chave proibida \"crons\"")
    return problemas


def verifica_polling(raiz: Path) -> list[str]:
    """setInterval no frontend e while True sem saída explícita no backend."""
    problemas = []

    for caminho in _arquivos(raiz, {".js", ".jsx", ".ts", ".tsx"}):
        if _ignorado(_relativo(caminho, raiz)):
            continue
        for numero, linha in enumerate(caminho.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _PADRAO_SETINTERVAL.search(linha):
                problemas.append(
                    f"{_relativo(caminho, raiz)}:{numero}: setInterval (polling de frontend): {linha.strip()}"
                )

    for caminho in _arquivos(raiz, {".py"}):
        rel = _relativo(caminho, raiz)
        if _ignorado(rel):
            continue
        linhas = caminho.read_text(encoding="utf-8", errors="replace").splitlines()
        for indice, linha in enumerate(linhas):
            if not _PADRAO_WHILE_TRUE.match(linha):
                continue
            # Procura uma saída (break/return/raise) no corpo do laço, ou seja,
            # nas linhas seguintes até a indentação voltar ao nível do while.
            nivel = len(linha) - len(linha.lstrip())
            tem_saida = False
            for seguinte in linhas[indice + 1:]:
                if not seguinte.strip():
                    continue
                if (len(seguinte) - len(seguinte.lstrip())) <= nivel:
                    break
                if _PADRAO_SAIDA.match(seguinte):
                    tem_saida = True
                    break
            if not tem_saida:
                problemas.append(
                    f"{rel}:{indice + 1}: while True sem break/return/raise "
                    f"(laço infinito): {linha.strip()}"
                )
    return problemas


def verifica_entregas(raiz: Path) -> list[str]:
    """Nenhum artefato de entrega fora da pasta canônica."""
    problemas = []
    for caminho in _arquivos(raiz, EXTENSAO_ENTREGA):
        rel = _relativo(caminho, raiz)
        if rel in IGNORAR_ARQUIVOS or caminho.name in ARQUIVOS_PERMITIDOS:
            continue
        # PARTES_PERMITIDAS são prefixos de caminho (ex.: "backend/templates"),
        # então comparam contra a string, não contra os componentes soltos.
        if any(rel.startswith(permitido + "/") for permitido in PARTES_PERMITIDAS):
            continue
        # Tudo dentro de uma pasta de entregas é esperado.
        if any("Entregas" in parte for parte in caminho.parts):
            continue
        problemas.append(f"{rel}: artefato de entrega fora de ~/Desktop/RelMeg - Entregas/")
    return problemas


VERIFICACOES = (
    ("BackgroundTask fora da exceção", verifica_background),
    ("chave \"crons\" em vercel.json", verifica_crons),
    ("polling / laço infinito", verifica_polling),
    ("entrega fora da pasta canônica", verifica_entregas),
)


def main(argv: list[str]) -> int:
    raiz = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent.parent
    print(f"Verificando contratos do AGENTS.md em: {raiz}\n")

    falhou = False
    for titulo, funcao in VERIFICACOES:
        problemas = funcao(raiz)
        if problemas:
            falhou = True
            print(f"VIOLA  {titulo}:")
            for problema in problemas:
                print(f"       {problema}")
        else:
            print(f"  OK   {titulo}")

    print()
    if falhou:
        print("FALHOU: uma ou mais guardas de arquitetura foram violadas.")
        return 1
    print("OK: todas as guardas de arquitetura passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
