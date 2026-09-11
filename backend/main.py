import hmac
import logging
import re
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from rate_limit import limiter
from config import settings
from servicos.modelo_base import contrato_disponivel
from servicos.extrator_tse import router as rotas_extrator_tse
from servicos.exportador_local import router as rotas_exportador_local
from servicos.exportador_pdf import router as rotas_exportador_pdf
from routers import (
    deputados,
    proposicoes,
    eventos,
    autores,
    frentes,
    monitoramento,
    dou,
    tse,
    ai,
    fachada,
    planilha,
    auditoria,
    hub,
)
from routers.senado import materias as senado_materias, comissoes as senado_comissoes

# ---------------------------------------------------------------------------
# Observabilidade — loguru (AGENTS.md: apenas filesystem local + stderr).
# Saída principal: stderr (terminal do uvicorn). Persistência em arquivo com
# rotação diária em backend/logs/relmeg_{DATA}.log, silenciosa em falha de
# escrita (ambientes efêmeros não devem derrubar a aplicação).
# ---------------------------------------------------------------------------


class _InterceptHandler(logging.Handler):
    """Roteia os logs do stdlib (uvicorn, gunicorn, slowapi) para o loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            nivel = logging.getLevelName(record.levelname)
        except ValueError:
            nivel = record.levelno
        logger.log(nivel, record.getMessage())


def _configurar_loguru() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    try:
        logger.add(
            settings.log_dir / "relmeg_{time:YYYY-MM-DD}.log",
            level=settings.log_level,
            rotation="00:00",
            retention="14 days",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        )
    except OSError:
        # Ambientes efêmeros sem escrita persistente: seguimos só com stderr.
        pass

    # Intercepta o logging padrão para que os logs do uvicorn/starlette também
    # apareçam formatados pelo loguru no terminal do servidor.
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for _nome in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        logging.getLogger(_nome).handlers.clear()
        logging.getLogger(_nome).propagate = False
        logging.getLogger(_nome).handlers = [_InterceptHandler()]


# Garante as pastas essenciais (entregas, cache, logs, templates) ANTES de
# configurar o loguru para que o destino do log estruturado já exista — apenas
# filesystem local, sem nenhuma consulta a API externa (conforme AGENTS.md).
settings.garantir_diretorios()
_configurar_loguru()
logger.info("RelMeg API iniciando — observabilidade via loguru (nível {})", settings.log_level)


def _validar_dependencias_criticas() -> None:
    """Validação FAIL-FAST de startup: dependências físicas SEM fallback.

    Apenas verificação de arquivos locais — nenhuma chamada a API externa
    (conforme AGENTS.md: handlers de startup/lifespan não podem consultar
    APIs externas, mas podem validar o filesystem).

    Se um arquivo crítico estiver ausente, a aplicação NÃO inicializa: derruba
    imediatamente com log CRÍTICO, em vez de falhar com 500 no meio de um
    relatório de última hora.
    """
    criticos = (
        (settings.modelo_clipping, "MODELO A SER SEGUIDO.docx (Clipping Semanal)"),
    )
    ausentes = [f"{nome} -> {caminho}" for caminho, nome in criticos if not caminho.exists()]
    if ausentes:
        logger.critical(
            "FALHA FATAL NO STARTUP — dependência crítica ausente: {}", "; ".join(ausentes)
        )
        raise RuntimeError(
            "Startup abortado: o template crítico está ausente. Restaure os "
            "arquivos em backend/templates/ e reinicie."
        )
    if settings.relmeg_requer_api_key and not settings.relmeg_api_key:
        logger.critical(
            "FALHA FATAL NO STARTUP — RELMEG_REQUER_API_KEY=true mas RELMEG_API_KEY "
            "está vazia. Configure a chave em backend/.env antes de expor a API."
        )
        raise RuntimeError(
            "Startup abortado: aplicação exige autenticação, mas a chave "
            "RELMEG_API_KEY não foi definida."
        )
    if not contrato_disponivel():
        logger.warning(
            "MODELO BASE ausente (contrato do operador e cópia interna) — "
            "será usado o gabarito canônico de fallback.",
        )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _validar_dependencias_criticas()
    logger.info("RelMeg API pronta — esperando comandos do operador (on-demand).")
    yield
    logger.info("RelMeg API encerrando — obrigado por usar o RelMeg!")


_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(
    title="RelMeg API",
    description="Back-end de monitoramento legislativo e stakeholder intelligence",
    lifespan=_lifespan,
    dependencies=[Security(_api_key_header)],
)

# ---------------------------------------------------------------------------
# Segurança de superfície (X-API-Key) — opcional por env (RELMEG_API_KEY)
# ---------------------------------------------------------------------------
# Se a chave estiver definida, TODAS as rotas (exceto infraestrutura) exigem o
# header. Se vazia, a API permanece aberta com aviso claro — apta apenas para
# ambiente localhost/desenvolvimento.
#
# /docs, /redoc e /openapi.json seguem a mesma regra por padrão (não expõem o
# esquema da API sem chave). Para abri-los em ambiente controlado, defina
# RELMEG_DOCS_PUBLICOS=true.

_CAMINHOS_ISENTOS_API_KEY = {"/", "/favicon.ico", "/healthz"}
if settings.relmeg_docs_publicos and settings.relmeg_api_key:
    _CAMINHOS_ISENTOS_API_KEY |= {
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
    }
    logger.info(
        "RELMEG_DOCS_PUBLICOS=true — /docs, /redoc e /openapi.json permanecem "
        "abertos mesmo com a API key ativa."
    )


class _VerificarApiKey(BaseHTTPMiddleware):
    """Rejeita 401 qualquer requisição sem o header X-API-Key válido."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in _CAMINHOS_ISENTOS_API_KEY:
            return await call_next(request)
        enviada = request.headers.get("X-API-Key") or ""
        # compare_digest: comparação em tempo constante (imune a timing attack).
        if not enviada or not hmac.compare_digest(enviada, settings.relmeg_api_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "API key ausente ou inválida. Envie o header X-API-Key."},
            )
        return await call_next(request)


if settings.relmeg_api_key:
    logger.info(
        "Autenticação X-API-Key ATIVA — todas as rotas exigem o header X-API-Key."
    )
else:
    logger.warning(
        "RELMEG_API_KEY não definida — API SEM AUTENTICAÇÃO. Configure-a em "
        "backend/.env antes de expor a API fora do localhost."
    )

# Rate limiting (slowapi): limite genérico para todas as rotas + limites
# específicos nas rotas pesadas via @limiter.limit (ver backend/rate_limit.py).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS restritivo: somente origens explicitamente autorizadas.
# NUNCA use allow_origins=["*"] em produção (invalida cookies/credentials).
# O domínio de produção e o ambiente local do Vite são fixos; origens extras
# podem ser adicionadas via variável de ambiente RELMEG_CORS_ORIGINS_EXTRA.
origens_padrao = [
    "https://relmegpina.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8082",
    "http://127.0.0.1:8082",
]
origens_configuradas = [
    origem.strip()
    for origem in settings.relmeg_cors_origins_extra.split(",")
    if origem.strip()
]

# Valida o FORMATO das origens em tempo de montagem: rejeita coringa ("*") e
# URLs sem esquema (protocolo) — CORS com allow_credentials=True combinado a
# "*" é inseguro, e origem sem https://http:// nunca chega a um browser válido.
def _validar_cors(origens: list) -> None:
    invalidas = [
        o for o in origens
        if "*" in o or not re.fullmatch(r"https?://[^\s,]+", o)
    ]
    if invalidas:
        raise ValueError(
            "Origem CORS inválida (use ex: https://app.exemplo.com): "
            + ", ".join(invalidas)
        )


_validar_cors(origens_padrao + origens_configuradas)
allow_origins = [*origens_padrao, *origens_configuradas]

# Ordem dos middlewares: Starlette empilha de trás para frente, então a CORS
# é adicionada por último para ficar como a camada mais externa (respostas 429
# e erros também recebem os cabeçalhos CORS corretos). A verificação de
# API-Key fica IMEDIATAMENTE dentro da CORS: instala o 401 máxima cedo (não
# consome rate-limit nem processamento) porém ainda exige o header real.
app.add_middleware(SlowAPIMiddleware)
if settings.relmeg_api_key:
    app.add_middleware(_VerificarApiKey)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "Accept"],
)

app.include_router(deputados.router)
app.include_router(proposicoes.router)
app.include_router(eventos.router)
app.include_router(autores.router)
app.include_router(frentes.router)
app.include_router(monitoramento.router)
app.include_router(dou.router)
app.include_router(senado_materias.router)
app.include_router(senado_comissoes.router)
app.include_router(rotas_extrator_tse)
app.include_router(rotas_exportador_local)
app.include_router(rotas_exportador_pdf)
app.include_router(tse.router)
app.include_router(ai.router)
app.include_router(fachada.router)
app.include_router(planilha.router)
app.include_router(auditoria.router)
app.include_router(hub.router)

@app.get("/healthz")
@limiter.limit("60/minute")
async def healthz(request: Request):
    """Health check para load balancers/orquestradores (sem rede externa).

    Valida o acesso ao banco SQLite local (uma leitura barata) e a existência
    dos templates críticos. Não consulta nenhuma API externa (AGENTS.md).
    """
    try:
        from database import init_db

        init_db()
        db_ok = True
    except Exception:
        db_ok = False
    try:
        template_ok = settings.modelo_clipping.exists()
    except Exception:
        template_ok = False

    http_status = 200 if (db_ok and template_ok) else 503
    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if http_status == 200 else "degradado",
            "banco": db_ok,
            "template_clipping": template_ok,
        },
    )


@app.get("/")
@limiter.limit("60/minute")
async def home(request: Request):
    return {"status": "ok", "mensagem": "Bem-vindo ao back-end do RelMeg modularizado!"}