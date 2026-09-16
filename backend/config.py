"""
Configurações centralizadas do RelMeg (pydantic-settings + pathlib).

Ponto único de verdade para diretórios, URLs de APIs públicas, TTL de cache,
limites de concorrência e parâmetros operacionais. Nenhum módulo pode mais
hardcodar caminhos ou configurações: tudo passa por ``settings``.

Carregamento:
    - Valores padrão tipados definidos abaixo;
    - Sobrescritos por variáveis de ambiente (ex.: DIR_ENTREGAS,
      TSE_BASE_URL, TSE_CACHE_TTL) e pelo arquivo backend/.env, se existir.

Validação inicial:
    - ``settings.garantir_diretorios()`` cria as pastas essenciais (entregas,
      cache, logs) se não existirem; é chamado na montagem da aplicação em
      main.py (sem tocar em APIs externas — ok mesmo com AGENTS.md).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Tuple

from pydantic_settings import BaseSettings, SettingsConfigDict

DIR_ARQUIVO = Path(__file__).resolve()
DIR_BACKEND = DIR_ARQUIVO.parent          # <repo>/backend
RAIZ_REPO = DIR_BACKEND.parent            # raiz do repositório
ARQUIVO_ENV = DIR_BACKEND / ".env"


class Configuracoes(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ARQUIVO_ENV),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Diretórios raiz
    # ------------------------------------------------------------------
    backend_dir: Path = DIR_BACKEND
    repo_root: Path = RAIZ_REPO

    # ------------------------------------------------------------------
    # Pasta de entregas ("RelMeg - Entregas", fora do repositório)
    # ------------------------------------------------------------------
    dir_entregas: Path = Path.home() / "Desktop" / "RelMeg - Entregas"
    subdir_tse: str = "TSE"
    subdir_novas_proposicoes: str = "Novas proposições"
    subdir_relatorios: str = "Relatórios"
    subdir_perfil: str = "Perfil"

    # ------------------------------------------------------------------
    # Cache local e logs
    # ------------------------------------------------------------------
    data_dir: Path = DIR_BACKEND / "data"
    log_dir: Path = DIR_BACKEND / "logs"
    relmeg_cache_db: Path = DIR_BACKEND / "data" / "relmeg_cache.db"
    # Nível de log da observabilidade (loguru). Sobrescrevível via env
    # LOG_LEVEL (o campo não tem prefixo; o pydantic-settings deriva o nome
    # do atributo). Em produção recomendado "INFO"; depuração: "DEBUG".
    log_level: str = "INFO"
    # Retenção do log estruturado de auditoria (dias). Valores <=0 desativam a
    # poda (o log cresceria indefinidamente). A poda roda dentro de
    # registrar_evento — manutenção casada com escrita sob demanda, NUNCA um
    # job agendado (AGENTS.md).
    auditoria_retencao_dias: int = 90

    # ------------------------------------------------------------------
    # Templates (100% portáteis — residem DENTRO do repositório, nunca em
    # caminho absoluto externo como Desktop/RelMeg - Entregas). Referências
    # relativas tornam a aplicação autossuficiente e copiável para qualquer
    # máquina/usuário do Windows sem quebrar a montagem dos documentos.
    # ------------------------------------------------------------------
    templates_dir: Path = DIR_BACKEND / "templates"
    # Clipping Semanal — CRÍTICO: sem fallback, aborte o startup (fail-fast).
    modelo_clipping: Path = templates_dir / "MODELO A SER SEGUIDO.docx"
    # Excel "MODELO BASE" do TSE — tem fallback embutido (gabarito canônico).
    modelo_base_template: Path = templates_dir / "MODELO BASE"

    # ------------------------------------------------------------------
    # Segurança de superfície (X-API-Key via header)
    # ------------------------------------------------------------------
    # Se definida (env RELMEG_API_KEY), TODAS as rotas exigem o header
    # "X-API-Key". Se vazia, a API roda apenas com aviso de segurança
    # (destinada exclusivamente a ambiente localhost/desenvolvimento).
    relmeg_api_key: str = ""
    # Quando RELMEG_REQUER_API_KEY=true e a chave estiver vazia, o startup
    # ABORTA (fail-fast) em vez de subir a API exposta sem autenticação.
    relmeg_requer_api_key: bool = False
    # Define se /docs, /redoc, /openapi.json ficam ABERTOS (públicos) mesmo com
    # a API key ativa. Padrão False = documentação protegida junto com as rotas.
    # Em ambiente localhost de desenvolvimento, abra com RELMEG_DOCS_PUBLICOS=true.
    relmeg_docs_publicos: bool = False

    # ------------------------------------------------------------------
    # Rate limit — confiança no cabeçalho X-Forwarded-For
    # ------------------------------------------------------------------
    # False (padrão): o rate limit usa o endereço real do socket, imune a XFF
    # forjado. True: confia no 1º endereço de X-Forwarded-For — use APENAS se o
    # deploy roda atrás de reverse-proxy controlado (Vercel, Render, nginx).
    confiar_xff: bool = False
    # Limite genérico (backstop) do slowapi aplicado às rotas NÃO decoradas
    # (ex.: "120/minute"). Sobrescrevível via env RATELIMIT_DEFAULT.
    ratelimit_default: str = "120/minute"

    # ------------------------------------------------------------------
    # TSE (DivulgaCandContas) — parâmetros operacionais
    # ------------------------------------------------------------------
    tse_base_url: str = "https://divulgacandcontas.tse.jus.br/divulga/rest/v1"
    tse_portal_url: str = "https://divulgacandcontas.tse.jus.br/divulga/"
    tse_timeout: float = 30.0
    # Concorrência máxima nas chamadas assíncronas de enriquecimento (protege
    # o rate-limit do TSE dentro de uma única requisição on-demand).
    tse_max_concurrency: int = 12
    # Retry com Exponential Backoff + Jitter (falhas de rede, timeouts e
    # bloqueios temporários 403/429/5xx). O atraso entre tentativas cresce como
    # base * 2**(n-1) e soma um jitter aleatório de 0..tse_backoff_jitter.
    tse_max_tentativas: int = 4
    tse_backoff_base: float = 1.0
    tse_backoff_jitter: float = 0.5
    # Janela de validade do cache local (segundos); ttl=0 desativa o reuso.
    tse_cache_ttl: int = 60 * 60 * 24
    tse_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
    # id_eleicao da API do DivulgaCandContas para as Eleições Gerais 2026.
    # Confirmado pelo operador em 16/09/2026 (varredura TSE GO — Deputado
    # Estadual). Sobrescrevível via env TSE_ID_ELEICAO_2026.
    tse_id_eleicao_2026: str = "20322002026"

    # ------------------------------------------------------------------
    # CORS (origens extras além das padrão de main.py)
    # ------------------------------------------------------------------
    relmeg_cors_origins_extra: str = ""

    # ------------------------------------------------------------------
    # Fallback por raspagem (Scrapling) — estepe quando a API falha
    # ------------------------------------------------------------------
    # True (padrão): conectores tentam raspar a página pública da fonte quando
    # a API oficial falha (403/503/timeout) ou devolve vazio, e a CLDF ganha o
    # histórico de andamento (a API dela só expõe a etapa atual). USO sempre
    # sob demanda (disparado por uma extração on-demand do operador — AGENTS.md).
    # False: desliga o fallback em todos os conectores (rotas seguem 4xx/5xx).
    usa_scrapling: bool = True
    # Tolerância do browser headless do Scrapling (em MILISSEGUNDOS).
    scrapling_timeout_ms: int = 45_000

    # ------------------------------------------------------------------
    # Camada HTTP universal do relmeg_core (conectores legislativos)
    # ------------------------------------------------------------------
    # Padrão de chamadas HTTP compartilhado por TODOS os conectores do motor
    # (Câmara, Senado, CLDF, ALGO, DOU...). O retry aplica Exponential Backoff
    # + Jitter sobre 403/429/5xx e erros de rede, como já é feito no TSE.
    http_timeout: float = 30.0
    http_max_tentativas: int = 3
    http_backoff_base: float = 0.5
    http_backoff_jitter: float = 0.5
    http_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @property
    def dir_tse(self) -> Path:
        """Pasta de entregas dos extratos TSE: <entregas>/TSE."""
        return self.dir_entregas / self.subdir_tse

    @property
    def dir_novas_proposicoes_pasta(self) -> Path:
        """Pasta do Clipping: <entregas>/Novas proposições."""
        return self.dir_entregas / self.subdir_novas_proposicoes

    @property
    def dir_relatorios(self) -> Path:
        """Pasta dos Relatórios Executivos PDF: <entregas>/Relatórios."""
        return self.dir_entregas / self.subdir_relatorios

    @property
    def dir_perfil(self) -> Path:
        """Pasta das planilhas de perfil/coleta de parlamentares: <entregas>/Perfil."""
        return self.dir_entregas / self.subdir_perfil

    def garantir_diretorios(self) -> Tuple[Path, ...]:
        """Cria (se ausentes) as pastas essenciais do projeto.

        Chamado na montagem da aplicação FastAPI. Nenhuma API externa é
        consultada aqui — apenas filesystem local.
        """
        pastas = (
            self.dir_entregas,
            self.dir_tse,
            self.dir_novas_proposicoes_pasta,
            self.dir_relatorios,
            self.dir_perfil,
            self.data_dir,
            self.log_dir,
            self.relmeg_cache_db.parent,
            self.templates_dir,
        )
        for pasta in pastas:
            pasta.mkdir(parents=True, exist_ok=True)
        return pastas


@lru_cache(maxsize=1)
def get_settings() -> Configuracoes:
    """Provedor de configurações (cachê único; usável como dependência DI)."""
    return Configuracoes()


# Singleton de conveniência importado pelos módulos consumidores.
settings = get_settings()