"""Configuração de ambiente para os testes unitários do RelMeg.

Define as variáveis de ambiente ANTES de qualquer import do backend (o singleton
settings de backend/config.py é montado no import do módulo). Usa diretórios
temporários para entregas e cache, e um TSE_BASE_URL inválido para garantir que
nenhum teste toque a rede real das APIs governamentais.

Além do isolamento de filesystem, duas fixtures ``autouse`` tornam isso um
INVARIANTE verificado pela suíte, e não uma convenção:

- ``_sem_rede_real``: bloqueia qualquer socket não-loopback. Um teste que
  esqueça de mockar o cliente HTTP falha na hora, em vez de gastar rate limit
  das APIs do governo e ainda passar.
- ``_banco_limpo_por_teste``: zera ``tse_execucoes`` entre os testes. Sem isso a
  suíte depende da ordem alfabética de execução (uma execução "Executando" de
  um teste envenenava o escopo lido por outro).
- ``_mocks_sempre_desligados``: desliga qualquer ``patch().start()`` remanescente,
  para que um teste que falha no meio não vaze mocks para os seguintes.
"""
import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

_RAIZ = Path(__file__).resolve().parent.parent
_BACKEND = _RAIZ / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_TMP = Path(tempfile.mkdtemp(prefix="relmeg_testes_"))

# Chaves reais do pydantic-settings de backend/config.py (sem env_prefix):
# dir_entregas -> DIR_ENTREGAS | relmeg_cache_db -> RELMEG_CACHE_DB
os.environ["RELMEG_CACHE_DB"] = str(_TMP / "cache_testes.db")
os.environ["DIR_ENTREGAS"] = str(_TMP / "Entregas")
os.environ["TSE_CACHE_TTL"] = "3600"
os.environ["TSE_BASE_URL"] = "http://tse.invalido.invalid/rest/v1"
os.environ["TSE_ID_ELEICAO_2026"] = "2055502026"
os.environ["RELMEG_CORS_ORIGINS_EXTRA"] = ""
# Desliga a autenticação X-API-Key na suíte (não enviamos header nos testes).
# O template do Clipping fica no default backend/templates (já versionado).
# Vazio EXPLÍCITO + RELMEG_REQUER_API_KEY=false = modo dev declarado (o padrão
# fail-closed abortaria o startup da suíte).
os.environ["RELMEG_API_KEY"] = ""
os.environ["RELMEG_REQUER_API_KEY"] = "false"
# Desliga o fallback Scrapling na suíte: nenhum teste deve abrir browser ou
# tocar a rede dos portais. Os testes específicos do fallback ligam por teste.
os.environ["USA_SCRAPLING"] = "false"


# ---------------------------------------------------------------------------
# Invariantes da suíte
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mocks_sempre_desligados():
    """Rede de segurança para mocks com ``patch().start()`` sem cleanup.

    Alguns testes usam o anti-padrão ``patch.object(...).start()``, que registra
    o patch em ``mock._patch._active_patches`` e só volta atrás se alguém chamar
    ``.stop()``. Se o teste falhar no meio, o mock vaza para os testes seguintes
    e a suíte passa a depender da ordem de execução. Esta fixture garante que
    nenhum patch sobreviva ao teste que o criou, venha ele de onde vier.
    """
    yield
    mock.patch.stopall()


def _e_loopback(host: object) -> bool:
    """True para destinos locais (127.0.0.0/8, ::1, localhost).

    Aceita string, tupla ``(host, porta)`` (formato de ``socket.connect``) ou
    objeto com atributo ``host`` (formato de ``create_connection``).
    """
    if isinstance(host, (tuple, list)) and host:
        host = host[0]
    if hasattr(host, "host"):  # objeto Address do create_connection
        host = getattr(host, "host")
    if not isinstance(host, str):
        return False
    if host in ("localhost", "", None):
        return True
    try:
        return socket.inet_pton(socket.AF_INET, host)[0] == 127
    except (OSError, ValueError):
        pass
    try:
        return socket.inet_pton(socket.AF_INET6, host) == socket.inet_pton(
            socket.AF_INET6, "::1"
        )
    except (OSError, ValueError):
        return False


@pytest.fixture(autouse=True)
def _sem_rede_real(monkeypatch):
    """Falha o teste se qualquer código tentar sair para a internet.

    Isola a rede das APIs governamentais por MECANISMO (o README trata isso
    como pré-requisito de segurança: nada de gastar rate limit do TSE/Câmara em
    teste). Testes que precisam de rede legam a mockar o cliente HTTP; a rota
    de loopback continua liberada para o TestClient do FastAPI.
    """

    _orig_connect = socket.socket.connect
    _orig_connect_ex = socket.socket.connect_ex
    _orig_create = socket.create_connection
    _orig_getaddrinfo = socket.getaddrinfo

    def _connect(self, destino, *args, **kwargs):
        if not _e_loopback(destino):
            raise AssertionError(
                f"Teste tentou acessar a rede real em {destino!r}. "
                "Mocke o cliente HTTP — a suíte do RelMeg roda com zero rede."
            )
        return _orig_connect(self, destino, *args, **kwargs)

    def _connect_ex(self, destino, *args, **kwargs):
        if not _e_loopback(destino):
            raise AssertionError(
                f"Teste tentou acessar a rede real em {destino!r}. "
                "Mocke o cliente HTTP — a suíte do RelMeg roda com zero rede."
            )
        return _orig_connect_ex(self, destino, *args, **kwargs)

    def _create_connection(destino, *args, **kwargs):
        host = destino[0] if isinstance(destino, tuple) else destino
        if not _e_loopback(host):
            raise AssertionError(
                f"Teste tentou acessar a rede real em {host!r}. "
                "Mocke o cliente HTTP — a suíte do RelMeg roda com zero rede."
            )
        return _orig_create(destino, *args, **kwargs)

    def _getaddrinfo(host, *args, **kwargs):
        if not _e_loopback(host):
            raise AssertionError(
                f"Teste tentou resolver um host externo ({host!r}). "
                "Mocke o cliente HTTP — a suíte do RelMeg roda com zero rede."
            )
        return _orig_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(socket, "create_connection", _create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    yield


@pytest.fixture(autouse=True)
def _banco_limpo_por_teste():
    """Zera o estado gravado entre testes (independência de ordem).

    Sem esta limpeza a suíte depende da ordem de execução. Duas fontes de
    vazamento já foram observadas:

    - ``tse_execucoes``: ``execucao_ativa_tse`` filtra por status sem TTL de
      obsolescência, então uma execução "Executando" deixada por um teste
      bloqueia o escopo para todos os testes seguintes da sessão.
    - ``relmeg_parlamentares`` / ``relmeg_proposicoes``: o hub persiste o que
      coleta, então um teste que coleta "senado/999999" faz outro teste que
      espera 404 receber 200. Foi o que a execução com ``--randomly-seed``
      expôs.

    ``tse_candidatos_raw`` / ``tse_candidato_detalhe`` NÃO são limpos: são o
    seed de TSE compartilhado entre testes, e apagá-los quebraria a suíte.
    """
    import database

    database.init_db()
    tabelas = (
        "tse_execucoes",
        "relmeg_tramitacoes",
        "relmeg_autorias",
        "relmeg_proposicoes",
        "relmeg_parlamentares",
        "auditoria_eventos",
    )
    with database._conectar() as con:  # noqa: SLF001 — higiene de teste
        for tabela in tabelas:
            con.execute(f"DELETE FROM {tabela}")
    yield