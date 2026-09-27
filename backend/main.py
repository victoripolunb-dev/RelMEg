import hmac
import logging
import re
import secrets
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


def _resolver_chave_efetiva() -> tuple[str, bool]:
    """Resolve a chave X-API-Key efetiva e se a autenticação deve ser exigida.

    Fail-closed por padrão (ver a tríade de estados em ``config.py``):

    - ``relmeg_api_key`` preenchida -> usa a chave, auth OBRIGATÓRIA;
    - ``relmeg_api_key == ""``       -> vazio explícito: modo dev sem auth, mas
      só é aceito com ``relmeg_requer_api_key=False``; caso contrário ABORTA;
    - ``relmeg_api_key is None``    -> nunca configurada: GERA uma chave
      aleatória efêmera por processo. Um deploy sem ``.env`` sobe protegido em
      vez de expor a API inteira; o operador lê a chave no log do startup.

    Retorna ``(chave_efetiva, auth_ativa)``.
    """
    bruta = settings.relmeg_api_key

    if bruta is None:  # nunca configurada -> chave efêmera
        if settings.relmeg_requer_api_key:
            chave = secrets.token_urlsafe(32)
            logger.warning(
                "RELMEG_API_KEY NÃO definida — foi gerada uma chave ALEATÓRIA "
                "para este processo (ela morre com o servidor). "
                "Defina RELMEG_API_KEY no .env para torná-la estável."
            )
            logger.info("Chave gerada (envie no header X-API-Key): {}", chave)
            return chave, True
        # Fail-closed desligado de propósito: roda aberto, com aviso máximo.
        logger.warning(
            "RELMEG_API_KEY não definida E RELMEG_REQUER_API_KEY=false — "
            "API SEM AUTENTICAÇÃO por decisão explícita do operador. NÃO exponha "
            "fora do localhost."
        )
        return "", False

    if not bruta:  # vazio explícito
        if settings.relmeg_requer_api_key:
            logger.critical(
                "FALHA FATAL NO STARTUP — RELMEG_API_KEY está vazia e "
                "RELMEG_REQUER_API_KEY=true (padrão). Defina a chave em "
                "backend/.env, ou declare RELMEG_REQUER_API_KEY=false para "
                "assumir explicitamente o risco de rodar sem autenticação."
            )
            raise RuntimeError(
                "Startup abortado: RELMEG_API_KEY vazia com "
                "RELMEG_REQUER_API_KEY=true (fail-closed)."
            )
        logger.warning(
            "RELMEG_API_KEY vazia (explícita) e RELMEG_REQUER_API_KEY=false — "
            "API SEM AUTENTICAÇÃO. Apenas localhost/desenvolvimento."
        )
        return "", False

    return bruta, True


# Garante as pastas essenciais (entregas, cache, logs, templates) ANTES de
# configurar o loguru para que o destino do log estruturado já exista — apenas
# filesystem local, sem nenhuma consulta a API externa (conforme AGENTS.md).
settings.garantir_diretorios()
_configurar_loguru()
logger.info("RelMeg API iniciando — observabilidade via loguru (nível {})", settings.log_level)

CHAVE_EFETIVA, AUTH_ATIVA = _resolver_chave_efetiva()


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
# Segurança de superfície (X-API-Key) — fail-closed (ver _resolver_chave_efetiva)
# ---------------------------------------------------------------------------
# A chave efetiva já foi resolvida na montagem do módulo: nunca há janela em que
# a API fique aberta por acidente. Se AUTH_ATIVA, TODAS as rotas (exceto
# infraestrutura) exigem o header X-API-Key.
#
# /docs, /redoc e /openapi.json seguem a mesma regra por padrão (não expõem o
# esquema da API sem chave). Para abri-los em ambiente controlado, defina
# RELMEG_DOCS_PUBLICOS=true.

_CAMINHOS_ISENTOS_API_KEY = {"/", "/favicon.ico", "/healthz"}
if settings.relmeg_docs_publicos and AUTH_ATIVA:
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
        if not enviada or not hmac.compare_digest(enviada, CHAVE_EFETIVA):
            return JSONResponse(
                status_code=401,
                content={"detail": "API key ausente ou inválida. Envie o header X-API-Key."},
            )
        return await call_next(request)


if AUTH_ATIVA:
    logger.info(
        "Autenticação X-API-Key ATIVA — todas as rotas exigem o header X-API-Key."
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

class _TetoCorpo(BaseHTTPMiddleware):
    """Rejeita 413 requisições cujo Content-Length exceda o teto global.

    Atua na BORDA, antes do parser multipart: o Starlette spoula o corpo inteiro
    em disco temporário antes de o endpoint rodar, então um teto aplicado só
    dentro do handler (file.read()) já consumiu o disco. Rejeitar aqui devolve
    413 sem nunca aceitar o corpo.
    """

    async def dispatch(self, request: Request, call_next):
        bruto = request.headers.get("content-length")
        if bruto:
            try:
                tamanho = int(bruto)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Header Content-Length inválido."},
                )
            if tamanho > settings.corpo_max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            f"Corpo da requisição excede o limite de "
                            f"{settings.corpo_max_bytes // (1024 * 1024)} MB."
                        )
                    },
                )
        return await call_next(request)


# Ordem dos middlewares: Starlette empilha de trás para frente, então a CORS
# é adicionada por último para ficar como a camada mais externa (respostas 429
# e erros também recebem os cabeçalhos CORS corretos). A verificação de
# API-Key fica IMEDIATAMENTE dentro da CORS: instala o 401 máxima cedo (não
# consome rate-limit nem processamento) porém ainda exige o header real.
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(_TetoCorpo)
if AUTH_ATIVA:
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