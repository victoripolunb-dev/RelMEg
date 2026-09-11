"""
Camada de persistência e cache local do RelMeg (SQLite).

Garante que os resultados de extrações pesadas (TSE) fiquem disponíveis mesmo
quando as APIs públicas governamentais estiverem instáveis ou estiverem
bloqueando acessos automatizados (ex.: HTTP 403 do TSE). Uma vez salva, a base
é reutilizada dentro de uma janela de validade (TTL) sem refazer as chamadas de
enriquecimento — resposta instantânea e proteção contra rate limits.

Decisão de engine: SQLite (stdlib) em vez de DuckDB.
    - Zero dependência adicional em ambiente serverless (nenhuma lib nativa).
    - O volume dos extratos é baixo (centenas/milhares de linhas) e bem servido
      por um banco transacional simples com WAL.
    - Toda a lógica está isolada nas funções deste módulo: trocar o engine por
      DuckDB depois é uma alteração localizada (INSERT/SELECT), sem impacto no
      extrator ou nas rotas.

Tabelas:
    - tse_candidatos_raw : linhas brutas de candidatos (1 coluna por chave do
      DataFrame BI), indexadas por cache_key.
    - tse_cache_execucoes: histórico/controle por filtro (ano, uf, cargo), TTL,
      origem da captura e timestamps.
    - relmeg_parlamentares / relmeg_proposicoes / relmeg_tramitacoes /
      relmeg_autorias: base legislativa normalizada do hub universal
      (fonte + id_externo como chave; autorias ligam parlamentar <-> proposição).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata as _ud
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from loguru import logger

from config import settings


class ErroBancoDados(Exception):
    """Falha real de acesso ao repositório SQLite (não é "dado não existente").

    Diferencia um problema de infraestrutura (corrupção, disco cheio, lock)
    de uma busca legítima sem resultado, que continua retornando None/[].
    As rotas traduzem isto em 500 em vez de 404 silencioso.
    """


def caminho_db() -> Path:
    """Caminho completo do arquivo do banco local (config.py / RELMEG_CACHE_DB)."""
    return settings.relmeg_cache_db


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_TABELA_CANDIDATOS = """CREATE TABLE IF NOT EXISTS tse_candidatos_raw (
    cache_key                TEXT NOT NULL,
    linha                    INTEGER NOT NULL,
    ano                      INTEGER,
    uf                       TEXT,
    cargo                    TEXT,
    id_candidato             TEXT,
    nome_urna                TEXT,
    nome_completo            TEXT,
    numero                   TEXT,
    partido_sigla            TEXT,
    situacao                 TEXT,
    ocupacao                 TEXT,
    genero                   TEXT,
    cor_raca                 TEXT,
    grau_instrucao           TEXT,
    estado_civil             TEXT,
    data_nascimento          TEXT,
    municipio                TEXT,
    coligacao                TEXT,
    total_bens_declarados    REAL,
    cpf                      TEXT,
    cnpj                     TEXT,
    foto_url                 TEXT,
    tem_detalhe              INTEGER,
    capturado_em             TEXT NOT NULL
)"""

_TABELA_EXECUCOES = """CREATE TABLE IF NOT EXISTS tse_cache_execucoes (
    cache_key        TEXT PRIMARY KEY,
    ano              INTEGER NOT NULL,
    uf               TEXT NOT NULL,
    codigo_cargo     INTEGER NOT NULL,
    municipio        TEXT,
    limite           INTEGER,
    origem           TEXT NOT NULL DEFAULT 'api',
    total_candidatos INTEGER NOT NULL DEFAULT 0,
    criado_em        TEXT NOT NULL,
    ultimo_acesso    TEXT NOT NULL,
    meta             TEXT
)"""

# Registro das execuções em segundo plano (jobs) de extração do TSE: estado e
# etapas para acompanhamento via rota de status (/tse/execucoes/{task_id}).
_TABELA_EXECUCOES_TSE = """CREATE TABLE IF NOT EXISTS tse_execucoes (
    task_id             TEXT PRIMARY KEY,
    ano                 INTEGER NOT NULL,
    uf                  TEXT NOT NULL,
    codigo_cargo        INTEGER NOT NULL,
    municipio           TEXT,
    limite              INTEGER,
    forcar_atualizacao  INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL,
    etapas              TEXT NOT NULL DEFAULT '[]',
    detalhe             TEXT,
    criado_em           TEXT NOT NULL,
    concluido_em        TEXT,
    total_candidatos    INTEGER,
    origem              TEXT,
    cache_key           TEXT,
    caminho_arquivo     TEXT
)"""

_TABELA_EVENTOS = """CREATE TABLE IF NOT EXISTS auditoria_eventos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT NOT NULL,
    detalhe     TEXT NOT NULL,
    criado_em   TEXT NOT NULL
)"""

# ---------------------------------------------------------------------------
# Base legislativa normalizada (relmeg_core — hub universal de inteligência)
# ---------------------------------------------------------------------------
# Chave composta (fonte, id_externo): o banco nunca sabe a origem do dado, mas
# sabe de ONDE ele veio (fonte canônica: "camara", "senado", "cldf", "algo"...).
# A ligação formal parlamentar <-> proposição fica para quando o BI pedir.

_TABELA_PARLAMENTARES = """CREATE TABLE IF NOT EXISTS relmeg_parlamentares (
    fonte            TEXT NOT NULL,
    id_externo       TEXT NOT NULL,
    nome_completo    TEXT NOT NULL,
    partido          TEXT,
    uf               TEXT NOT NULL,
    nome_urna        TEXT,
    mandato_inicio   TEXT,
    mandato_fim      TEXT,
    status_ativo     INTEGER NOT NULL DEFAULT 1,
    cargo            TEXT,
    email            TEXT,
    url_foto         TEXT,
    url_perfil       TEXT,
    url_origem       TEXT,
    capturado_em     TEXT NOT NULL,
    PRIMARY KEY (fonte, id_externo)
)"""

_TABELA_PROPOSICOES = """CREATE TABLE IF NOT EXISTS relmeg_proposicoes (
    fonte             TEXT NOT NULL,
    id_externo        TEXT NOT NULL,
    sigla_tipo        TEXT NOT NULL,
    numero            INTEGER NOT NULL,
    ano               INTEGER NOT NULL,
    ementa            TEXT NOT NULL,
    orgao_origem      TEXT NOT NULL,
    autor_principal   TEXT,
    pauta_tematica    TEXT,
    situacao          TEXT,
    comissao_atual    TEXT,
    relator           TEXT,
    data_apresentacao TEXT,
    status_sn         TEXT,
    url_documento     TEXT,
    url_origem        TEXT,
    capturado_em      TEXT NOT NULL,
    PRIMARY KEY (fonte, id_externo)
)"""

_TABELA_TRAMITACOES = """CREATE TABLE IF NOT EXISTS relmeg_tramitacoes (
    fonte                 TEXT NOT NULL,
    id_proposicao_externo TEXT NOT NULL,
    data_evento           TEXT,
    orgao_local           TEXT,
    descricao_fase        TEXT,
    status                TEXT NOT NULL,
    sequencia             INTEGER,
    despacho              TEXT,
    url_evento            TEXT,
    capturado_em          TEXT NOT NULL,
    PRIMARY KEY (fonte, id_proposicao_externo, data_evento, sequencia, status)
)"""

_TABELA_AUTORIAS = """CREATE TABLE IF NOT EXISTS relmeg_autorias (
    fonte                TEXT NOT NULL,
    proposicao_id_externo TEXT NOT NULL,
    autor_nome           TEXT NOT NULL,
    autor_parlamentar_fonte TEXT,
    autor_id_externo     TEXT,
    principal            INTEGER NOT NULL DEFAULT 0,
    capturado_em         TEXT NOT NULL,
    PRIMARY KEY (fonte, proposicao_id_externo, autor_nome)
)"""

_TABELA_DETALHE_TSE = """CREATE TABLE IF NOT EXISTS tse_candidato_detalhe (
    cache_key     TEXT NOT NULL,
    id_candidato  TEXT NOT NULL,
    payload       TEXT NOT NULL,
    capturado_em  TEXT NOT NULL,
    PRIMARY KEY (cache_key, id_candidato)
)"""

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_tse_raw_chave ON tse_candidatos_raw (cache_key)",
    "CREATE INDEX IF NOT EXISTS idx_tse_raw_filtro ON tse_candidatos_raw (ano, uf, cargo)",
    "CREATE INDEX IF NOT EXISTS idx_tse_exec_concluido ON tse_execucoes (concluido_em)",
    "CREATE INDEX IF NOT EXISTS idx_eventos_tipo ON auditoria_eventos (tipo, criado_em)",
    "CREATE INDEX IF NOT EXISTS idx_prop_fonte_ano ON relmeg_proposicoes (fonte, ano, sigla_tipo)",
    "CREATE INDEX IF NOT EXISTS idx_tram_prop ON relmeg_tramitacoes (fonte, id_proposicao_externo)",
    "CREATE INDEX IF NOT EXISTS idx_auto_prop ON relmeg_autorias (proposicao_id_externo)",
    "CREATE INDEX IF NOT EXISTS idx_auto_parl ON relmeg_autorias (autor_id_externo)",
]

# As 22 chaves do DataFrame BI (COLUNAS_BI em servicos/extrator_tse.py) — nomeiam
# exatamente as colunas da tabela bruta.
CHAVES_DF = [
    "ano", "uf", "cargo", "id_candidato", "nome_urna", "nome_completo",
    "numero", "partido_sigla", "situacao", "ocupacao", "genero", "cor_raca",
    "grau_instrucao", "estado_civil", "data_nascimento", "municipio",
    "coligacao", "total_bens_declarados", "cpf", "cnpj", "foto_url",
    "tem_detalhe",
]
_CHAVES_SQL = ", ".join(CHAVES_DF)

_init_lock = threading.Lock()
_ini_ok = False


def _agora_utc() -> str:
    """Timestamp ISO-8601 em UTC (armazenamento e comparação de TTL)."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conectar() -> Iterator[sqlite3.Connection]:
    """Abre conexão SQLite (WAL) com TRINDADE TRANSACIONAL garantida.

    Padrão obrigatório da arquitetura: yield → commit → rollback → close.
    - Yield: disponibiliza a conexão ao corpo do bloco ``with``;
    - commit(): grava a transação de forma atômica se o corpo for concluído
      sem erro (redundante/simples quando a própria função já commita);
    - rollback(): reverte QUALQUER operação pendente se uma exceção for
      disparada no meio da escrita — previne corrupção de dados (arquivo DB
      nunca fica pela metade);
    - close(): libera a conexão/trava no arquivo mesmo com exceção —
      thread-safety, evita "database is locked" quando BackgroundTasks são
      abortadas/encerradas abruptamente.
    """
    caminho = caminho_db()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(caminho), timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    """Cria as tabelas e índices (idempotente), lazily na primeira utilização.

    Inclui a MIGRAÇÃO de versões antigas: o ``CREATE TABLE IF NOT EXISTS`` não
    altera tabelas já existentes, então colunas novas (``tse_execucoes.origem``,
    ``cache_key``, ``caminho_arquivo``, ``tse_cache_execucoes.origem``) são
    adicionadas via ``ALTER TABLE`` quando faltarem em bancos pré-existentes.
    """
    global _ini_ok, _init_lock
    if _ini_ok:
        return
    with _init_lock:
        if _ini_ok:
            return
        with _conectar() as con:
            con.executescript(
                _TABELA_CANDIDATOS + ";\n" + _TABELA_EXECUCOES + ";\n" +
                _TABELA_EXECUCOES_TSE + ";\n" + _TABELA_EVENTOS + ";\n" +
                _TABELA_PARLAMENTARES + ";\n" + _TABELA_PROPOSICOES + ";\n" +
                _TABELA_TRAMITACOES + ";\n" + _TABELA_AUTORIAS + ";\n" +
                _TABELA_DETALHE_TSE + ";\n" +
                ";\n".join(_INDICES)
            )
            _migrar_esquema_antigo(con)
        _ini_ok = True


def _migrar_esquema_antigo(con: sqlite3.Connection) -> None:
    """Adiciona colunas novas a bancos criados antes do schema atual."""
    alteracoes = []

    cols = {linha[1] for linha in con.execute("PRAGMA table_info(tse_execucoes)")}
    for ddl in ("origem TEXT", "cache_key TEXT", "caminho_arquivo TEXT"):
        if ddl.split()[0] not in cols:
            con.execute(f"ALTER TABLE tse_execucoes ADD COLUMN {ddl}")
            alteracoes.append(ddl)

    cols_cache = {linha[1] for linha in con.execute("PRAGMA table_info(tse_cache_execucoes)")}
    if "origem" not in cols_cache:
        con.execute("ALTER TABLE tse_cache_execucoes ADD COLUMN origem TEXT NOT NULL DEFAULT 'api'")
        alteracoes.append("origem (tse_cache_execucoes)")

    if alteracoes:
        con.execute("PRAGMA user_version = 2")
        logger.info(
            "database: migração aplicada — colunas adicionadas: {cols}", cols=", ".join(alteracoes)
        )


# ---------------------------------------------------------------------------
# Meta de execução / TTL
# ---------------------------------------------------------------------------

def info_cache(cache_key: str) -> Optional[Dict[str, Any]]:
    """Retorna os metadados da última execução para a chave, ou None."""
    try:
        init_db()
        with _conectar() as con:
            linha = con.execute(
                "SELECT * FROM tse_cache_execucoes WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            return dict(linha) if linha else None
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: info_cache falhou para {k}: {e}", k=cache_key, e=exc)
        return None


def cache_fresco(cache_key: str, ttl_segundos: int) -> bool:
    """True se há uma execução salva para a chave dentro da janela do TTL."""
    if not ttl_segundos or ttl_segundos <= 0:
        return False
    info = info_cache(cache_key)
    if not info:
        return False
    try:
        criado = datetime.fromisoformat(info["criado_em"])
        agora = datetime.now(timezone.utc)
        if criado.tzinfo is None:
            criado = criado.replace(tzinfo=timezone.utc)
        return (agora - criado).total_seconds() < ttl_segundos
    except (ValueError, TypeError):
        return False


def registrar_uso(cache_key: str) -> None:
    """Atualiza o último acesso (auditoria/depuração de hit no cache)."""
    try:
        init_db()
        with _conectar() as con:
            con.execute(
                "UPDATE tse_cache_execucoes SET ultimo_acesso = ? WHERE cache_key = ?",
                (_agora_utc(), cache_key),
            )
    except (sqlite3.Error, OSError) as exc:
        logger.debug("database: registrar_uso falhou para {k}: {e}", k=cache_key, e=exc)


# ---------------------------------------------------------------------------
# Escrita / leitura dos candidatos brutos
# ---------------------------------------------------------------------------

def _normalizar_valor(chave: str, valor: Any) -> Any:
    """Aplica coerção de tipo por coluna antes de gravar no SQLite."""
    if chave == "total_bens_declarados":
        if valor is None:
            return None
        try:
            return round(float(valor), 2)
        except (TypeError, ValueError):
            return None
    if chave == "tem_detalhe":
        return 1 if valor else 0
    if chave in ("ano",):
        try:
            return int(valor)
        except (TypeError, ValueError):
            return None
    if valor is None:
        return None
    return str(valor)


def salvar_candidatos_tse(
    cache_key: str,
    ano: int,
    uf: str,
    codigo_cargo: int,
    municipio: Optional[str],
    limite: Optional[int],
    candidatos: List[Dict[str, Any]],
    origem: str = "api",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    """(Re)grava a base bruta de candidatos e o registro de execução.

    Substituição atômica por chave: um novo pull elimina as linhas antigas da
    mesma extração dentro da mesma transação (nunca fica metade velha/metade
    nova servindo de cache).
    """
    init_db()
    capturado = _agora_utc()
    with _conectar() as con:
        con.execute("BEGIN")
        con.execute("DELETE FROM tse_candidatos_raw WHERE cache_key = ?", (cache_key,))
        con.executemany(
            "INSERT INTO tse_candidatos_raw (cache_key, linha, " + _CHAVES_SQL +
            ", capturado_em) VALUES (?, ?, " +
            ", ".join("?" for _ in CHAVES_DF) + ", ?)",
            [
                (cache_key, i,
                 *[_normalizar_valor(chave, candidato.get(chave)) for chave in CHAVES_DF],
                 capturado)
                for i, candidato in enumerate(candidatos)
            ],
        )
        con.execute(
            "INSERT INTO tse_cache_execucoes "
            "(cache_key, ano, uf, codigo_cargo, municipio, limite, origem, "
            " total_candidatos, criado_em, ultimo_acesso, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET "
            "ano = excluded.ano, uf = excluded.uf, "
            "codigo_cargo = excluded.codigo_cargo, municipio = excluded.municipio, "
            "limite = excluded.limite, origem = excluded.origem, "
            "total_candidatos = excluded.total_candidatos, "
            "criado_em = excluded.criado_em, ultimo_acesso = excluded.ultimo_acesso, "
            "meta = excluded.meta",
            (
                cache_key, ano, uf.upper(), codigo_cargo, municipio, limite,
                origem, len(candidatos), capturado, capturado,
                meta and json.dumps(meta, ensure_ascii=False, default=str) or None,
            ),
        )


def carregar_candidatos_tse(cache_key: str) -> Optional[List[Dict[str, Any]]]:
    """Lê a base salva para a chave como lista de dicts (22 chaves do BI).

    Retorna None se não houver nenhuma linha gravada (nunca uma lista vazia para
    diferenciar "sem cache" de "extração legítima sem candidatos").
    """
    try:
        init_db()
        with _conectar() as con:
            linhas = con.execute(
                "SELECT " + _CHAVES_SQL + " FROM tse_candidatos_raw "
                "WHERE cache_key = ? ORDER BY linha ASC",
                (cache_key,),
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: carregar_candidatos_tse falhou para {k}: {e}", k=cache_key, e=exc)
        return None
    if not linhas:
        return None
    return [dict(linha) for linha in linhas]


def salvar_detalhe_candidatos(cache_key: str, candidatos) -> None:
    """Persiste o detalhe rico (máximo) de cada candidato do lote.

    Recebe a mesma lista usada na extração; registra apenas quem trouxe o bloco
    ``_detalhe_rico``, sem quebrar o fluxo se algum vier vazio.
    """
    try:
        init_db()
        registros = []
        for candidato in candidatos:
            dados = candidato if isinstance(candidato, dict) else getattr(candidato, "model_dump", lambda: dict(candidato))()
            rico = dados.get("_detalhe_rico")
            if not rico:
                continue
            id_candidato = dados.get("id_candidato")
            if id_candidato is None:
                continue
            registros.append(
                (
                    cache_key,
                    str(id_candidato),
                    json.dumps(rico, ensure_ascii=False, default=str),
                    _agora_utc(),
                )
            )
        if not registros:
            return
        with _conectar() as con:
            con.executemany(
                "INSERT OR REPLACE INTO tse_candidato_detalhe "
                "(cache_key, id_candidato, payload, capturado_em) "
                "VALUES (?, ?, ?, ?)",
                registros,
            )
    except (sqlite3.Error, OSError, TypeError) as exc:
        logger.warning("database: salvar_detalhe_candidatos falhou para {k}: {e}", k=cache_key, e=exc)


def buscar_detalhe_candidato(cache_key: str, id_candidato: str) -> Optional[Dict[str, Any]]:
    """Devolve o detalhe rico salvo de um candidato (ou None)."""
    try:
        init_db()
        with _conectar() as con:
            linha = con.execute(
                "SELECT payload, capturado_em FROM tse_candidato_detalhe "
                "WHERE cache_key = ? AND id_candidato = ?",
                (cache_key, str(id_candidato)),
            ).fetchone()
            if not linha:
                return None
            payload = json.loads(linha["payload"])
            payload["capturado_em"] = linha["capturado_em"]
            return payload
    except (sqlite3.Error, OSError, ValueError) as exc:
        logger.warning("database: buscar_detalhe_candidato falhou ({k}/{id}): {e}", k=cache_key, id=id_candidato, e=exc)
        return None


# ---------------------------------------------------------------------------
# Registro de execuções em segundo plano (jobs de extração do TSE)
# ---------------------------------------------------------------------------

def criar_execucao_tse(
    task_id: str,
    ano: int,
    uf: str,
    codigo_cargo: int,
    municipio: Optional[str] = None,
    limite: Optional[int] = None,
    forcar_atualizacao: bool = False,
) -> None:
    """Abre um registro de execução com status inicial 'Iniciado'."""
    init_db()
    agora = _agora_utc()
    with _conectar() as con:
        con.execute(
            "INSERT OR REPLACE INTO tse_execucoes "
            "(task_id, ano, uf, codigo_cargo, municipio, limite, "
            " forcar_atualizacao, status, etapas, criado_em, concluido_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (task_id, ano, uf.upper(), codigo_cargo, municipio, limite,
             1 if forcar_atualizacao else 0, "Iniciado", "[]", agora),
        )
        con.execute(
            "UPDATE tse_execucoes SET etapas = ? WHERE task_id = ?",
            (json.dumps([f"{agora} Iniciado"], ensure_ascii=False), task_id),
        )


def atualizar_execucao_tse(
    task_id: str,
    *,
    status: Optional[str] = None,
    etapa: Optional[str] = None,
    detalhe: Optional[str] = None,
    total_candidatos: Optional[int] = None,
    origem: Optional[str] = None,
    cache_key: Optional[str] = None,
    caminho_arquivo: Optional[str] = None,
) -> None:
    """Atualiza status/etapa (append no log de etapas) de uma execução.

    Falhas de escrita nunca devem quebrar o worker: engolem sqlite.Error.
    """
    try:
        init_db()
        with _conectar() as con:
            agora = _agora_utc()
            campos = []
            parametros: list = []
            if status:
                campos.append("status = ?")
                parametros.append(status)
                if status in ("Concluído", "Falhou"):
                    campos.append("concluido_em = ?")
                    parametros.append(agora)
            if detalhe is not None:
                campos.append("detalhe = ?")
                parametros.append(str(detalhe)[:2000])
            if total_candidatos is not None:
                campos.append("total_candidatos = ?")
                parametros.append(total_candidatos)
            if origem:
                campos.append("origem = ?")
                parametros.append(origem)
            if cache_key:
                campos.append("cache_key = ?")
                parametros.append(cache_key)
            if caminho_arquivo is not None:
                campos.append("caminho_arquivo = ?")
                parametros.append(str(caminho_arquivo))

            if etapa:
                campos.append("etapas = json_insert(etapas, '$[#]', ?)")
                parametros.append(f"{agora} {etapa}")

            parametros.append(task_id)
            if campos:
                con.execute(
                    "UPDATE tse_execucoes SET " + ", ".join(campos) +
                    " WHERE task_id = ?",
                    parametros,
                )
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: atualizar_execucao_tse falhou p/ {task}: {e}", task=task_id, e=exc)


def obter_execucao_tse(task_id: str) -> Optional[Dict[str, Any]]:
    """Retorna o registro completo de uma execução (ou None)."""
    try:
        init_db()
        with _conectar() as con:
            linha = con.execute(
                "SELECT * FROM tse_execucoes WHERE task_id = ?", (task_id,)
            ).fetchone()
            if linha is None:
                return None
            registro = dict(linha)
            try:
                registro["etapas"] = json.loads(registro.get("etapas") or "[]")
            except (TypeError, ValueError):
                registro["etapas"] = []
            return registro
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: obter_execucao_tse falhou p/ {task}: {e}", task=task_id, e=exc)
        return None


def listar_execucoes_tse(limite: int = 20) -> List[Dict[str, Any]]:
    """Lista as execuções mais recentes (para a rota geral de status)."""
    try:
        init_db()
        with _conectar() as con:
            linhas = con.execute(
                "SELECT task_id, ano, uf, codigo_cargo, status, criado_em, "
                "       concluido_em, total_candidatos, origem "
                "FROM tse_execucoes ORDER BY criado_em DESC LIMIT ?",
                (int(limite),),
            ).fetchall()
            return [dict(r) for r in linhas]
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: listar_execucoes_tse falhou: {e}", e=exc)
        return []


def execucao_ativa_tse(
    ano: int,
    uf: str,
    codigo_cargo: int,
    municipio: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retorna a execução em andamento para o mesmo escopo (ou None).

    Usada pelo trigger para recusar (409) disparo concorrente, conforme
    exigência do AGENTS.md.
    """
    try:
        init_db()
        with _conectar() as con:
            linha = con.execute(
                "SELECT task_id, status, criado_em FROM tse_execucoes "
                "WHERE ano = ? AND uf = ? AND codigo_cargo = ? "
                "  AND IFNULL(municipio, '') = IFNULL(?, '') "
                "  AND status IN ('Iniciado', 'Executando') "
                "ORDER BY criado_em DESC LIMIT 1",
                (ano, uf.upper(), codigo_cargo, municipio),
            ).fetchone()
            return dict(linha) if linha else None
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: execucao_ativa_tse falhou p/ {ano}/{uf}: {e}", ano=ano, uf=uf, e=exc)
        return None


def iniciar_execucao_tse(
    task_id: str,
    ano: int,
    uf: str,
    codigo_cargo: int,
    municipio: Optional[str] = None,
    limite: Optional[int] = None,
    forcar_atualizacao: bool = False,
) -> Optional[Dict[str, Any]]:
    """Cria a execução do TSE de forma ATÔMICA (check + insert na mesma transação).

    Sem esta função, o padrão verificar-depois-inserir do trigger deixava uma
    janela TOCTOU: dois disparos concorrentes para o mesmo escopo passavam
    pela checagem antes que qualquer um houvesse persistido, quebrando a recusa
    de concorrência (409) exigida pelo AGENTS.md.

    Sob ``BEGIN IMMEDIATE`` o segundo escritor bloqueia (busy_timeout=30s) até o
    primeiro commitar, enxerga a execução ativa e é recusado.

    Retorna a execução ativa existente (para o trigger responder 409) ou None
    quando o registro foi criado com sucesso.
    """
    init_db()
    agora = _agora_utc()
    with _conectar() as con:
        con.execute("BEGIN IMMEDIATE")
        ativa = con.execute(
            "SELECT task_id, status, criado_em FROM tse_execucoes "
            "WHERE ano = ? AND uf = ? AND codigo_cargo = ? "
            "  AND IFNULL(municipio, '') = IFNULL(?, '') "
            "  AND status IN ('Iniciado', 'Executando') "
            "ORDER BY criado_em DESC LIMIT 1",
            (ano, uf.upper(), codigo_cargo, municipio),
        ).fetchone()
        if ativa:
            return dict(ativa)
        con.execute(
            "INSERT OR REPLACE INTO tse_execucoes "
            "(task_id, ano, uf, codigo_cargo, municipio, limite, "
            " forcar_atualizacao, status, etapas, criado_em, concluido_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (task_id, ano, uf.upper(), codigo_cargo, municipio, limite,
             1 if forcar_atualizacao else 0, "Iniciado", "[]", agora),
        )
        con.execute(
            "UPDATE tse_execucoes SET etapas = ? WHERE task_id = ?",
            (json.dumps([f"{agora} Iniciado"], ensure_ascii=False), task_id),
        )
        return None


# ---------------------------------------------------------------------------
# Auditoria e registro estruturado de eventos
# ---------------------------------------------------------------------------

def registrar_evento(tipo: str, detalhe: str) -> None:
    """Registra um evento estruturado (Family Talks, Modelo Base, falhas).

    Falhas de escrita nunca devem quebrar o fluxo: engole sqlite.Error.
    Ex.: registrar_evento("family_talks", "37 aprovadas, 12 descartadas").

    A poda da retenção (auditoria_retencao_dias) ocorre aqui mesmo, na escrita
    sob demanda — nunca em job agendado (AGENTS.md).
    """
    try:
        init_db()
        with _conectar() as con:
            con.execute(
                "INSERT INTO auditoria_eventos (tipo, detalhe, criado_em) "
                "VALUES (?, ?, ?)",
                (tipo, str(detalhe)[:2000], _agora_utc()),
            )
            retencao = settings.auditoria_retencao_dias
            if retencao and retencao > 0:
                corte = (datetime.now(timezone.utc) - timedelta(days=retencao)).isoformat()
                con.execute(
                    "DELETE FROM auditoria_eventos WHERE criado_em < ?",
                    (corte,),
                )
                con.execute(
                    "DELETE FROM tse_candidato_detalhe WHERE capturado_em < ?",
                    (corte,),
                )
    except (sqlite3.Error, OSError) as exc:
        logger.debug("database: registrar_evento falhou ({t}): {e}", t=tipo, e=exc)


def listar_eventos(limite: int = 50) -> List[Dict[str, Any]]:
    """Lista os eventos estruturados mais recentes (auditoria)."""
    try:
        init_db()
        with _conectar() as con:
            linhas = con.execute(
                "SELECT id, tipo, detalhe, criado_em FROM auditoria_eventos "
                "ORDER BY id DESC LIMIT ?",
                (max(1, int(limite)),),
            ).fetchall()
            return [dict(r) for r in linhas]
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: listar_eventos falhou: {e}", e=exc)
        return []


def resumo_metricas_tse() -> Dict[str, Any]:
    """Resumo operacional das extrações (taxa de sucesso, falhas, tempo médio)."""
    try:
        init_db()
        with _conectar() as con:
            linhas = con.execute(
                "SELECT status, criado_em, concluido_em, total_candidatos "
                "FROM tse_execucoes",
            ).fetchall()
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: resumo_metricas_tse falhou: {e}", e=exc)
        linhas = []

    total = len(linhas)
    concluidas = sum(1 for r in linhas if r["status"] == "Concluído")
    falhas = sum(1 for r in linhas if r["status"] == "Falhou")
    executando = sum(1 for r in linhas if r["status"] in ("Iniciado", "Executando"))

    def _segundos(criado: Optional[str], concluido: Optional[str]) -> Optional[float]:
        if not criado or not concluido:
            return None
        try:
            return round((datetime.fromisoformat(concluido) - datetime.fromisoformat(criado)).total_seconds(), 1)
        except (TypeError, ValueError):
            return None

    tempos = [
        t for t in (_segundos(r["criado_em"], r["concluido_em"]) for r in linhas if r["status"] == "Concluído")
        if t is not None
    ]
    totais_ok = [r["total_candidatos"] for r in linhas if r["status"] == "Concluído" and r["total_candidatos"] is not None]

    return {
        "total_execucoes": total,
        "em_execucao": executando,
        "concluidas": concluidas,
        "falhas": falhas,
        "taxa_sucesso": round((concluidas / total * 100), 1) if total else 0.0,
        "tempo_medio_segundos": (round(sum(tempos) / len(tempos), 1) if tempos else None),
        "total_candidatos_processados": sum(totais_ok) if totais_ok else 0,
    }


def historico_execucoes_tse(limite: int = 50) -> List[Dict[str, Any]]:
    """Panorama detalhado das últimas execuções (status, tempos, arquivo)."""
    try:
        init_db()
        with _conectar() as con:
            linhas = con.execute(
                "SELECT task_id, ano, uf, codigo_cargo, municipio, status, "
                "       criado_em, concluido_em, total_candidatos, origem, "
                "       cache_key, caminho_arquivo, detalhe, etapas "
                "FROM tse_execucoes ORDER BY criado_em DESC LIMIT ?",
                (max(1, int(limite)),),
            ).fetchall()
            registros = []
            for r in linhas:
                reg = dict(r)
                try:
                    reg["etapas"] = json.loads(reg.get("etapas") or "[]")
                except (TypeError, ValueError):
                    reg["etapas"] = []
                registros.append(reg)
            return registros
    except (sqlite3.Error, OSError) as exc:
        logger.warning("database: historico_execucoes_tse falhou: {e}", e=exc)
        return []


# ---------------------------------------------------------------------------
# Base legislativa normalizada (hub universal — salvar/buscar/listar)
# ---------------------------------------------------------------------------
# Entradas são SEMPRE os Modelos Pydantic de ``relmeg_core.models.schemas``
# (o banco conhece o contrato, nunca o formato da fonte). Datas/timestamps são
# gravados como ISO-8601; booleanos como 0/1.

_CAMPOS_PARLAMENTAR = (
    "fonte", "id_externo", "nome_completo", "partido", "uf", "nome_urna",
    "mandato_inicio", "mandato_fim", "status_ativo", "cargo", "email",
    "url_foto", "url_perfil", "url_origem", "capturado_em",
)
_CAMPOS_PROPOSICAO = (
    "fonte", "id_externo", "sigla_tipo", "numero", "ano", "ementa",
    "orgao_origem", "autor_principal", "pauta_tematica", "situacao",
    "comissao_atual", "relator", "data_apresentacao", "status_sn",
    "url_documento", "url_origem", "capturado_em",
)
_CAMPOS_TRAMITACAO = (
    "fonte", "id_proposicao_externo", "data_evento", "orgao_local",
    "descricao_fase", "status", "sequencia", "despacho", "url_evento",
    "capturado_em",
)


def _valor_sql(valor: Any) -> Any:
    """Coage valores do modelo Pydantic para o formato SQLite (ISO/0-1)."""
    if isinstance(valor, bool):
        return 1 if valor else 0
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, dict):
        return json.dumps(valor, ensure_ascii=False, default=str)
    return valor


def _linha_modelo(modelo, campos: Sequence[str]) -> tuple:
    """Serializa um modelo Pydantic na ordem das colunas da tabela."""
    dados = getattr(modelo, "model_dump", lambda: dict(modelo))()
    return tuple(_valor_sql(dados.get(campo)) for campo in campos)


def salvar_parlamentar(parlamentar) -> None:
    """Upsert de um ParlamentarModel (repositório relmeg_parlamentares).

    Aproveita a gravação para ligar autorias pendentes que correspondam ao nome
    (autoria formal parlamentar <-> proposição, o elo que o BI pede).
    """
    init_db()
    dados = getattr(parlamentar, "model_dump", lambda: dict(parlamentar))()
    colunas = [f'"{c}"' for c in _CAMPOS_PARLAMENTAR]
    alvo = ", ".join(colunas)
    valores = ", ".join("?" for _ in _CAMPOS_PARLAMENTAR)
    atualiza = ", ".join(
        f'"{c}" = excluded."{c}"' for c in _CAMPOS_PARLAMENTAR
        if c not in ("fonte", "id_externo")
    )
    fonte = dados.get("fonte")
    id_externo = str(dados.get("id_externo"))
    with _conectar() as con:
        con.execute(
            f"INSERT INTO relmeg_parlamentares ({alvo}) VALUES ({valores}) "
            f"ON CONFLICT(fonte, id_externo) DO UPDATE SET {atualiza}",
            _linha_modelo(parlamentar, _CAMPOS_PARLAMENTAR),
        )
        if fonte:
            _resolver_autoria(
                con, fonte, id_externo,
                [dados.get("nome_completo"), dados.get("nome_urna")],
            )


def _normalizar_nome_autor(nome: Any) -> str:
    """Normaliza um nome p/ correspondência: caixa alta, sem acentos, sem prefixo."""
    texto = _ud.normalize("NFD", str(nome or "").upper())
    texto = "".join(ch for ch in texto if _ud.category(ch) != "Mn")
    texto = re.sub(r"^(DEP\.|DEPUTAD[OA]|SENADOR(A)?|PARLAMENTAR)\s+", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _autorias_do_projeto(dados: Dict[str, Any]) -> List[Dict[str, str]]:
    """Transforma integrantes/autor_principal do modelo em registros de autoria.

    Retorna uma lista com ``autor_nome`` e ``principal`` (0/1). Sem integrantes,
    usa o ``autor_principal`` isolado quando existir.
    """
    integrantes = dados.get("integrantes") or []
    integrantes = [str(n) for n in integrantes if str(n or "").strip()]
    principal_nome = dados.get("autor_principal")
    if not integrantes:
        if not str(principal_nome or "").strip():
            return []
        integrantes = [str(principal_nome)]
    nomes: List[str] = []
    for nome in integrantes:
        if nome not in nomes:
            nomes.append(nome)
    principal = nomes[0] if principal_nome not in nomes or not str(principal_nome or "").strip() else str(principal_nome)
    return [
        {"autor_nome": nome, "principal": 1 if nome == principal else 0}
        for nome in nomes
    ]


def _resolver_autoria(con: sqlite3.Connection, fonte: str, id_externo: str, nomes: List[str]) -> None:
    """Liga autorias não resolvidas ao parlamentar recém-salvo (por nome normalizado)."""
    if not nomes:
        return
    alvos = [_normalizar_nome_autor(n) for n in nomes if str(n or "").strip()]
    if not alvos:
        return
    linhas = con.execute(
        "SELECT autor_nome FROM relmeg_autorias "
        "WHERE fonte = ? AND autor_id_externo IS NULL",
        (fonte,),
    ).fetchall()
    para_ligar = []
    for linha in linhas:
        if _normalizar_nome_autor(linha["autor_nome"]) in alvos:
            para_ligar.append((fonte, str(id_externo), fonte, linha["autor_nome"]))
    con.executemany(
        "UPDATE relmeg_autorias SET autor_parlamentar_fonte = ?, autor_id_externo = ? "
        "WHERE fonte = ? AND autor_nome = ?",
        para_ligar,
    )


def salvar_projeto(projeto) -> None:
    """Upsert de um ProjetoDeLeiModel (repositório relmeg_proposicoes).

    Também substitui as autorias da proposição (integrantes), no mesmo escopo
    transacional — nunca servir metade velha/metade nova.
    """
    init_db()
    dados = getattr(projeto, "model_dump", lambda: dict(projeto))()
    colunas = [f'"{c}"' for c in _CAMPOS_PROPOSICAO]
    alvo = ", ".join(colunas)
    valores = ", ".join("?" for _ in _CAMPOS_PROPOSICAO)
    atualiza = ", ".join(
        f'"{c}" = excluded."{c}"' for c in _CAMPOS_PROPOSICAO
        if c not in ("fonte", "id_externo")
    )
    autorias = _autorias_do_projeto(dados)
    with _conectar() as con:
        con.execute(
            f"INSERT INTO relmeg_proposicoes ({alvo}) VALUES ({valores}) "
            f"ON CONFLICT(fonte, id_externo) DO UPDATE SET {atualiza}",
            _linha_modelo(projeto, _CAMPOS_PROPOSICAO),
        )
        con.execute(
            "DELETE FROM relmeg_autorias WHERE fonte = ? AND proposicao_id_externo = ?",
            (dados["fonte"], str(dados["id_externo"])),
        )
        if autorias:
            con.executemany(
                "INSERT OR IGNORE INTO relmeg_autorias "
                "(fonte, proposicao_id_externo, autor_nome, autor_parlamentar_fonte, "
                " autor_id_externo, principal, capturado_em) "
                "VALUES (?, ?, ?, NULL, NULL, ?, ?)",
                [
                    (dados["fonte"], str(dados["id_externo"]), a["autor_nome"],
                     a["principal"], _agora_utc())
                    for a in autorias
                ],
            )


def salvar_tramitacoes(fonte: str, id_externo: str, tramitacoes) -> None:
    """Substitui o histórico de tramitação de uma proposição (transação atômica).

    Mesmo padrão dos candidatos: DELETE no escopo + INSERT na mesma transação,
    para nunca servir metade velha/metade nova como cache.
    """
    init_db()
    colunas = [f'"{c}"' for c in _CAMPOS_TRAMITACAO]
    alvo = ", ".join(colunas)
    valores = ", ".join("?" for _ in _CAMPOS_TRAMITACAO)
    with _conectar() as con:
        con.execute("BEGIN")
        con.execute(
            "DELETE FROM relmeg_tramitacoes WHERE fonte = ? AND id_proposicao_externo = ?",
            (fonte, str(id_externo)),
        )
        con.executemany(
            f"INSERT OR IGNORE INTO relmeg_tramitacoes ({alvo}) VALUES ({valores})",
            [_linha_modelo(t, _CAMPOS_TRAMITACAO) for t in tramitacoes],
        )


def buscar_parlamentar(fonte: str, id_externo: str) -> Optional[Dict[str, Any]]:
    """Retorna um parlamentar salvo (ou None)."""
    try:
        init_db()
        with _conectar() as con:
            linha = con.execute(
                "SELECT * FROM relmeg_parlamentares WHERE fonte = ? AND id_externo = ?",
                (fonte, str(id_externo)),
            ).fetchone()
            return dict(linha) if linha else None
    except (sqlite3.Error, OSError) as exc:
        logger.error("database: buscar_parlamentar falhou ({f}/{id}): {e}", f=fonte, id=id_externo, e=exc)
        raise ErroBancoDados(f"falha ao ler parlamentar {fonte}/{id_externo}") from exc


def buscar_projeto(fonte: str, id_externo: str) -> Optional[Dict[str, Any]]:
    """Retorna uma proposição salva (ou None)."""
    try:
        init_db()
        with _conectar() as con:
            linha = con.execute(
                "SELECT * FROM relmeg_proposicoes WHERE fonte = ? AND id_externo = ?",
                (fonte, str(id_externo)),
            ).fetchone()
            return dict(linha) if linha else None
    except (sqlite3.Error, OSError) as exc:
        logger.error("database: buscar_projeto falhou ({f}/{id}): {e}", f=fonte, id=id_externo, e=exc)
        raise ErroBancoDados(f"falha ao ler proposição {fonte}/{id_externo}") from exc


def buscar_tramitacoes(fonte: str, id_externo: str) -> List[Dict[str, Any]]:
    """Retorna o histórico salvo de uma proposição (vazio se nada gravado)."""
    try:
        init_db()
        with _conectar() as con:
            linhas = con.execute(
                "SELECT * FROM relmeg_tramitacoes "
                "WHERE fonte = ? AND id_proposicao_externo = ? "
                "ORDER BY IFNULL(data_evento, ''), IFNULL(sequencia, 0)",
                (fonte, str(id_externo)),
            ).fetchall()
            return [dict(r) for r in linhas]
    except (sqlite3.Error, OSError) as exc:
        logger.error("database: buscar_tramitacoes falhou ({f}/{id}): {e}", f=fonte, id=id_externo, e=exc)
        raise ErroBancoDados(f"falha ao ler tramitações {fonte}/{id_externo}") from exc


def listar_autorias(fonte: str, id_externo: str) -> List[Dict[str, Any]]:
    """Autores de uma proposição (ligação formal; autor_id_externo vazio = não resolvido)."""
    try:
        init_db()
        with _conectar() as con:
            linhas = con.execute(
                "SELECT autor_nome, autor_parlamentar_fonte, autor_id_externo, principal "
                "FROM relmeg_autorias "
                "WHERE fonte = ? AND proposicao_id_externo = ? "
                "ORDER BY principal DESC, autor_nome",
                (fonte, str(id_externo)),
            ).fetchall()
            return [dict(r) for r in linhas]
    except (sqlite3.Error, OSError) as exc:
        logger.error("database: listar_autorias falhou ({f}/{id}): {e}", f=fonte, id=id_externo, e=exc)
        raise ErroBancoDados(f"falha ao ler autorias de {fonte}/{id_externo}") from exc


def listar_autorias_de_parlamentar(fonte: str, id_externo: str) -> List[Dict[str, Any]]:
    """Proposições que o parlamentar co-autorou (via ligação formal resolvida)."""
    try:
        init_db()
        with _conectar() as con:
            linhas = con.execute(
                "SELECT p.fonte, p.id_externo, p.sigla_tipo, p.numero, p.ano, "
                "       p.ementa, p.situacao, a.principal "
                "FROM relmeg_autorias a "
                "JOIN relmeg_proposicoes p "
                "  ON p.fonte = a.fonte AND p.id_externo = a.proposicao_id_externo "
                "WHERE a.autor_parlamentar_fonte = ? AND a.autor_id_externo = ? "
                "ORDER BY p.ano DESC, p.numero DESC",
                (fonte, str(id_externo)),
            ).fetchall()
            return [dict(r) for r in linhas]
    except (sqlite3.Error, OSError) as exc:
        logger.error("database: listar_autorias_de_parlamentar falhou ({f}/{id}): {e}", f=fonte, id=id_externo, e=exc)
        raise ErroBancoDados(f"falha ao ler proposições de {fonte}/{id_externo}") from exc


def _escape_like(termo: str) -> str:
    """Escapa curingas de LIKE (%, _) e o próprio caractere de escape."""
    return (
        str(termo)
        .replace("\\", "\\\\")
        .replace("%", r"\%")
        .replace("_", r"\_")
    )


def listar_proposicoes(
    *,
    fonte: Optional[str] = None,
    ano: Optional[int] = None,
    tipo: Optional[str] = None,
    termo: Optional[str] = None,
    limite: int = 200,
) -> List[Dict[str, Any]]:
    """Lista proposições salvas com filtros opcionais (inteligência/clipping).

    ``termo`` busca em ementa, autor, pauta e situação (LIKE case-insensitive,
    com curingas escapados — ``%``/``_`` no termo são literais, não padrão).
    Sem filtros, devolve as mais recentes por ``capturado_em``.
    """
    try:
        init_db()
        clausulas: List[str] = []
        parametros: list = []
        if fonte:
            clausulas.append("fonte = ?")
            parametros.append(fonte)
        if ano is not None:
            clausulas.append("ano = ?")
            parametros.append(int(ano))
        if tipo:
            clausulas.append("sigla_tipo = ?")
            parametros.append(str(tipo).upper())
        if termo:
            clausulas.append(
                "(ementa LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR autor_principal LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR pauta_tematica LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR situacao LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            padrao = f"%{_escape_like(termo)}%"
            parametros.extend([padrao, padrao, padrao, padrao])
        where = (" WHERE " + " AND ".join(clausulas)) if clausulas else ""
        with _conectar() as con:
            linhas = con.execute(
                "SELECT * FROM relmeg_proposicoes" + where +
                " ORDER BY capturado_em DESC, ano DESC LIMIT ?",
                (*parametros, max(1, int(limite))),
            ).fetchall()
            return [dict(r) for r in linhas]
    except (sqlite3.Error, OSError) as exc:
        logger.error("database: listar_proposicoes falhou: {e}", e=exc)
        raise ErroBancoDados("falha ao listar proposições") from exc


def listar_parlamentares(
    *,
    fonte: Optional[str] = None,
    uf: Optional[str] = None,
    partido: Optional[str] = None,
    termo: Optional[str] = None,
    limite: int = 200,
) -> List[Dict[str, Any]]:
    """Lista parlamentares salvos com filtros opcionais (inteligência/stakeholders).

    ``termo`` busca nome (civil/urna); ``uf`` e ``partido`` filtram exatos
    (case-insensitive). Curingas ``%``/``_`` no termo são literais (escapados).
    Sem filtros, devolve os mais recentes por ``capturado_em``.
    """
    try:
        init_db()
        clausulas: List[str] = []
        parametros: list = []
        if fonte:
            clausulas.append("fonte = ?")
            parametros.append(fonte)
        if uf:
            clausulas.append("uf = ?")
            parametros.append(str(uf).strip().upper())
        if partido:
            clausulas.append("partido = ?")
            parametros.append(str(partido).strip().upper())
        if termo:
            clausulas.append(
                "(nome_completo LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR nome_urna LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            padrao = f"%{_escape_like(termo)}%"
            parametros.extend([padrao, padrao])
        where = (" WHERE " + " AND ".join(clausulas)) if clausulas else ""
        with _conectar() as con:
            linhas = con.execute(
                "SELECT * FROM relmeg_parlamentares" + where +
                " ORDER BY capturado_em DESC, nome_completo ASC LIMIT ?",
                (*parametros, max(1, int(limite))),
            ).fetchall()
            return [dict(r) for r in linhas]
    except (sqlite3.Error, OSError) as exc:
        logger.error("database: listar_parlamentares falhou: {e}", e=exc)
        raise ErroBancoDados("falha ao listar parlamentares") from exc