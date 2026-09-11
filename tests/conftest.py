"""Configuração de ambiente para os testes unitários do RelMeg.

Define as variáveis de ambiente ANTES de qualquer import do backend (o singleton
settings de backend/config.py é montado no import do módulo). Usa diretórios
temporários para entregas e cache, e um TSE_BASE_URL inválido para garantir que
nenhum teste toque a rede real das APIs governamentais.
"""
import os
import sys
import tempfile
from pathlib import Path

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
os.environ["RELMEG_API_KEY"] = ""
# Desliga o fallback Scrapling na suíte: nenhum teste deve abrir browser ou
# tocar a rede dos portais. Os testes específicos do fallback ligam por teste.
os.environ["USA_SCRAPLING"] = "false"