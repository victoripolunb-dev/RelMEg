"""
Rotas do Hub Legislativo (relmeg_core) — expõe o orquestrador ao operador.

Somente ações EXPLÍCITAS disparam extração (AGENTS.md):
    - POST /hub/proposicoes/{fonte}/{id} → o gatilho on-demand (coleta + persiste);
    - GET  lê do repositório local (nunca toca a API externa);
    - GET  /hub/fontes → status das fontes (sem rede).

O rate limit de extração (slowapi) fica NESTA rota de disparo, de acordo com a
regra do AGENTS.md ("manter os rate limits na rota de disparo").
"""
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import asyncio

import httpx

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from config import settings
from rate_limit import limiter, LIMITE_PROPOSICOES
from relmeg_core.connectors.base_connector import BuscaNaoSuportada, FonteSemApiPublica
from relmeg_core.connectors.dou import DOUSemFichaEstruturada
from relmeg_core.orquestrador import obter_orquestrador
from relmeg_core.utils.helpers import modulo_database


router = APIRouter(prefix="/hub", tags=["Hub Legislativo"])


def _serializar(modelo: Any) -> Dict[str, Any]:
    """JSON-serializável a partir de um modelo Pydantic (ou dict simples)."""
    dump = getattr(modelo, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return dict(modelo)


def _erro_extracao(exc: Exception) -> HTTPException:
    """Traduz exceções de extração em respostas HTTP claras."""
    if isinstance(exc, (FonteSemApiPublica, BuscaNaoSuportada, DOUSemFichaEstruturada)):
        return HTTPException(status_code=501, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
        return HTTPException(
            status_code=404,
            detail="Registro não encontrado na fonte consultada (404).",
        )
    logger.warning("hub: extração falhou — {tipo}: {e}", tipo=type(exc).__name__, e=exc)
    return HTTPException(
        status_code=502,
        detail="Falha ao consultar a fonte externa. A fonte pode estar instável "
        "ou bloqueando a requisição — tente novamente em instantes.",
    )


def _erro_repositorio(db: Any) -> HTTPException:
    """Traduz falhas de acesso ao repositório SQLite em 500 (não 404)."""
    return HTTPException(
        status_code=500,
        detail="Falha interna ao acessar o repositório local. Tente novamente.",
    )


@router.get("/fontes")
async def status_fontes() -> Dict[str, str]:
    """Status das fontes registradas no motor (nenhuma chamada externa)."""
    return obter_orquestrador().fontes_disponiveis()


@router.get("/busca/proposicoes")
@limiter.limit(LIMITE_PROPOSICOES)
async def buscar_proposicoes_hub(
    request: Request,
    fonte: str = Query(..., min_length=2, max_length=20, description="Fonte canônica: camara, senado, cldf, dou"),
    termo: str = Query(..., min_length=2, max_length=150, description="Palavras-chave da busca"),
    sigla_tipo: Optional[str] = Query(None, min_length=2, max_length=10, description="Sigla: PL, PEC, REQ..."),
    ano: Optional[int] = Query(None, ge=1900, le=2100, description="Janela opcional de apresentação"),
    itens: int = Query(25, ge=1, le=100, description="Quantidade máxima de resultados"),
) -> Dict[str, Any]:
    """Busca proposições por palavras-chave na fonte (chamada EXTERNA sob demanda).

    Retorna resumos normalizados SEM persistir. Para coleta/persistência do
    payload completo, dispare POST /hub/proposicoes/{fonte}/{id_externo} por
    resultado de interesse. Fontes sem busca via API (ex.: ALGO) respondem 501.
    A fonte ``dou`` devolve publicações do Diário Oficial (resumos ricos, sem
    coleta por item — são a própria entrega da busca).
    """
    fonte = fonte.strip().lower()
    orquestrador = obter_orquestrador()
    try:
        resultados = await orquestrador.buscar_proposicoes(
            fonte, termo, sigla_tipo=sigla_tipo, ano=ano, itens=itens
        )
    except (FonteSemApiPublica, BuscaNaoSuportada, DOUSemFichaEstruturada) as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("hub: busca de proposições falhou — {tipo}: {e}", tipo=type(exc).__name__, e=exc)
        raise HTTPException(
            status_code=502,
            detail="Falha ao consultar a fonte externa. A fonte pode estar instável "
            "ou bloqueando a requisição — tente novamente em instantes.",
        ) from exc
    return {
        "fonte": fonte,
        "termo": termo,
        "total": len(resultados),
        "resultados": resultados,
    }


@router.get("/proposicoes/listar")
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_proposicoes(
    request: Request,
    fonte: Optional[str] = Query(None, description="Fonte canônica: camara, senado, cldf"),
    ano: Optional[int] = Query(None, ge=1900, le=2100),
    tipo: Optional[str] = Query(None, min_length=2, max_length=10, description="Sigla: PL, PEC, REQ..."),
    termo: Optional[str] = Query(None, min_length=2, max_length=150, description="Busca em ementa/autor/pauta"),
    limite: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """Lista proposições SALVAS no repositório local (nunca chama API externa)."""
    db = modulo_database()
    try:
        itens = await asyncio.to_thread(
            db.listar_proposicoes,
            fonte=fonte, ano=ano, tipo=tipo, termo=termo, limite=limite,
        )
    except db.ErroBancoDados:
        raise _erro_repositorio(db) from None
    return {"total": len(itens), "proposicoes": itens}


@router.get("/proposicoes/{fonte}/{id_externo:path}")
async def obter_proposicao_salva(fonte: str, id_externo: str) -> Dict[str, Any]:
    """Lê uma proposição + tramitações do repositório local (sem rede).

    404 se ainda não coletada — o operador deve disparar via POST.
    O id pode conter barras (CLDF usa o formato "PL 2473/2026").
    """
    fonte = fonte.strip().lower()
    db = modulo_database()
    try:
        projeto = await asyncio.to_thread(db.buscar_projeto, fonte, id_externo)
        if projeto is None:
            raise HTTPException(
                status_code=404,
                detail=f"Proposição {fonte}/{id_externo} ainda não coletada. Use POST /hub/proposicoes/{fonte}/{quote(id_externo, safe='')}.",
            )
        tramitacoes = await asyncio.to_thread(db.buscar_tramitacoes, fonte, id_externo)
        autorias = await asyncio.to_thread(db.listar_autorias, fonte, id_externo)
    except db.ErroBancoDados:
        raise _erro_repositorio(db) from None
    return {
        "fonte": fonte,
        "id_externo": projeto["id_externo"],
        "projeto": projeto,
        "tramitacoes": tramitacoes,
        "autorias": autorias,
    }


@router.post("/proposicoes/{fonte}/{id_externo:path}", status_code=201)
@limiter.limit(LIMITE_PROPOSICOES)
async def coletar_proposicao(
    request: Request,
    fonte: str,
    id_externo: str,
    campos: Optional[str] = Query(
        None,
        description="Campos desejados separados por vírgula (busca seletiva); vazio = volume completo",
    ),
) -> Dict[str, Any]:
    """GATILHO SOB DEMANDA: coleta a proposição na fonte e persiste no repositório.

    Chama externamente (Câmara/Senado/CLDF) APENAS quando o operador dispara.
    "algo" responde 501 (fonte indisponível na V1).
    """
    orquestrador = obter_orquestrador()
    campos_lista: Optional[List[str]] = (
        [c.strip() for c in campos.split(",") if c.strip()] if campos else None
    )

    try:
        resultado = await orquestrador.coletar_completo(
            fonte, id_externo, campos=campos_lista
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hub: extração de {fonte}/{id} falhou: {e}", fonte=fonte, id=id_externo, e=exc)
        raise _erro_extracao(exc) from exc

    return {
        "fonte": resultado["fonte"],
        "id_externo": resultado["projeto"].id_externo,
        "projeto": _serializar(resultado["projeto"]),
        "tramitacoes": [_serializar(t) for t in resultado["tramitacoes"]],
    }


@router.get("/parlamentares/listar")
@limiter.limit(LIMITE_PROPOSICOES)
async def listar_parlamentares(
    request: Request,
    fonte: Optional[str] = Query(None, description="Fonte canônica: camara, senado, cldf"),
    uf: Optional[str] = Query(None, min_length=2, max_length=2, description="UF do mandato (ex.: DF)"),
    partido: Optional[str] = Query(None, min_length=2, max_length=10, description="Sigla do partido (ex.: PL)"),
    termo: Optional[str] = Query(None, min_length=2, max_length=120, description="Busca no nome civil/urna"),
    limite: int = Query(100, ge=1, le=500),
) -> Dict[str, Any]:
    """Lista parlamentares SALVOS no repositório local (nunca chama API externa)."""
    db = modulo_database()
    try:
        itens = await asyncio.to_thread(
            db.listar_parlamentares,
            fonte=fonte, uf=uf, partido=partido, termo=termo, limite=limite,
        )
    except db.ErroBancoDados:
        raise _erro_repositorio(db) from None
    return {"total": len(itens), "parlamentares": itens}


@router.get("/parlamentares/{fonte}/{id_externo:path}")
async def obter_parlamentar_salvo(fonte: str, id_externo: str) -> Dict[str, Any]:
    """Lê um parlamentar do repositório local (sem rede).

    404 se ainda não coletado — o operador deve disparar via POST.
    """
    fonte = fonte.strip().lower()
    db = modulo_database()
    try:
        registro = await asyncio.to_thread(db.buscar_parlamentar, fonte, id_externo)
        if registro is None:
            raise HTTPException(
                status_code=404,
                detail=f"Parlamentar {fonte}/{id_externo} ainda não coletado. Use POST /hub/parlamentares/{fonte}/{quote(id_externo, safe='')}.",
            )
        proposicoes = await asyncio.to_thread(
            db.listar_autorias_de_parlamentar, fonte, id_externo
        )
    except db.ErroBancoDados:
        raise _erro_repositorio(db) from None
    return {
        "fonte": fonte,
        "parlamentar": registro,
        "proposicoes_autoradas": proposicoes,
    }


@router.post("/parlamentares/{fonte}/{id_externo:path}", status_code=201)
@limiter.limit(LIMITE_PROPOSICOES)
async def coletar_parlamentar(
    request: Request,
    fonte: str,
    id_externo: str,
    campos: Optional[str] = Query(
        None,
        description="Campos desejados separados por vírgula (busca seletiva); vazio = volume completo",
    ),
) -> Dict[str, Any]:
    """GATILHO SOB DEMANDA: coleta o parlamentar na fonte e persiste no repositório.

    Chama externamente (Câmara/Senado) APENAS quando o operador dispara.
    "algo" responde 501 (fonte indisponível na V1).
    """
    orquestrador = obter_orquestrador()
    campos_lista: Optional[List[str]] = (
        [c.strip() for c in campos.split(",") if c.strip()] if campos else None
    )

    try:
        parlamentar = await orquestrador.coletar_parlamentar(
            fonte, id_externo, campos=campos_lista
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hub: extração de parlamentar {fonte}/{id} falhou: {e}", fonte=fonte, id=id_externo, e=exc)
        raise _erro_extracao(exc) from exc

    return {"fonte": parlamentar.fonte, "parlamentar": _serializar(parlamentar)}


@router.post("/exportar/ficha", status_code=201)
@limiter.limit("10/minute")
async def exportar_ficha(
    request: Request,
    fonte: str = Query(..., min_length=2, max_length=20, description="Fonte canônica: camara, senado, cldf"),
    id_externo: str = Query(..., min_length=1, description="ID da proposição (Camara/Senado numérico; CLDF 'PL 2473/2026')"),
    baixar: bool = Query(False, description="True = devolve o .docx como download"),
) -> Any:
    """Gera a Ficha Legislativa (.docx) de uma proposição JÁ coletada.

    Lê APENAS o repositório local (sem chamada externa). Se a proposição ainda
    não foi coletada, retorna 404 — o operador deve disparar antes via
    POST /hub/proposicoes/{fonte}/{id_externo}.
    Grava em ~/Desktop/RelMeg - Entregas/Relatórios/ (formato padrão da casa).
    """
    from relmeg_core.exportador_docx import FichaLegislativaError, gerar_ficha

    fonte = fonte.strip().lower()
    db = modulo_database()
    try:
        projeto = await asyncio.to_thread(db.buscar_projeto, fonte, id_externo)
        if projeto is None:
            raise HTTPException(
                status_code=404,
                detail=f"Proposição {fonte}/{id_externo} ainda não coletada. Dispare POST /hub/proposicoes/{fonte}/{quote(id_externo, safe='')} antes de exportar.",
            )
        tramitacoes = await asyncio.to_thread(db.buscar_tramitacoes, fonte, id_externo)
    except db.ErroBancoDados:
        raise _erro_repositorio(db) from None

    try:
        resultado = gerar_ficha(projeto, tramitacoes)
    except FichaLegislativaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not baixar:
        return {k: v for k, v in resultado.items() if k != "caminho"}

    from fastapi.responses import FileResponse

    caminho = Path(resultado["caminho"])
    if not caminho.exists():
        raise HTTPException(status_code=500, detail="O arquivo não pôde ser gravado em disco.")
    return FileResponse(
        caminho,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=resultado["arquivo"],
    )


@router.post("/exportar/ficha-parlamentar", status_code=201)
@limiter.limit("10/minute")
async def exportar_ficha_parlamentar(
    request: Request,
    fonte: str = Query(..., min_length=2, max_length=20, description="Fonte canônica: camara, senado"),
    id_externo: str = Query(..., min_length=1, description="ID do parlamentar na fonte"),
    baixar: bool = Query(False, description="True = devolve o .docx como download"),
) -> Any:
    """Gera a Ficha de Parlamentar (.docx) de alguém JÁ coletado.

    Lê APENAS o repositório local (sem chamada externa). Se ainda não coletado,
    retorna 404 — o operador deve disparar antes via
    POST /hub/parlamentares/{fonte}/{id_externo}.
    """
    from relmeg_core.exportador_docx import FichaLegislativaError, gerar_ficha_parlamentar

    fonte = fonte.strip().lower()
    db = modulo_database()
    try:
        registro = await asyncio.to_thread(db.buscar_parlamentar, fonte, id_externo)
        if registro is None:
            raise HTTPException(
                status_code=404,
                detail=f"Parlamentar {fonte}/{id_externo} ainda não coletado. Dispare POST /hub/parlamentares/{fonte}/{quote(id_externo, safe='')} antes de exportar.",
            )
    except db.ErroBancoDados:
        raise _erro_repositorio(db) from None

    try:
        resultado = gerar_ficha_parlamentar(registro)
    except FichaLegislativaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not baixar:
        return {k: v for k, v in resultado.items() if k != "caminho"}

    from fastapi.responses import FileResponse

    caminho = Path(resultado["caminho"])
    if not caminho.exists():
        raise HTTPException(status_code=500, detail="O arquivo não pôde ser gravado em disco.")
    return FileResponse(
        caminho,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=resultado["arquivo"],
    )


@router.post("/exportar/planilha-coleta", status_code=201)
@limiter.limit("10/minute")
async def exportar_planilha_coleta(
    request: Request,
    fonte: Optional[str] = Query(None, min_length=2, max_length=20, description="Fonte canônica: camara, senado, cldf"),
    uf: Optional[str] = Query(None, min_length=2, max_length=2, description="UF do mandato (ex.: DF)"),
    partido: Optional[str] = Query(None, min_length=2, max_length=10, description="Sigla do partido (ex.: PL)"),
    termo: Optional[str] = Query(None, min_length=2, max_length=120, description="Filtra pelo nome civil/urna"),
    limite: int = Query(500, ge=1, le=1000, description="Quantidade máxima de linhas"),
    baixar: bool = Query(False, description="True = devolve o .xlsx como download"),
) -> Any:
    """Gera a Planilha de Coleta de Perfil (.xlsx no padrão MODELO BASE).

    Lê APENAS os parlamentares JÁ COLETADOS no repositório local (nunca chama
    API externa). Pré-preenche Casa/Nome/Partido/UF; os campos de coleta manual
    (celular, assessoria, contato, perfil...) ficam em branco para o trabalho
    de campo. Grava em ~/Desktop/RelMeg - Entregas/Perfil/.
    """
    from servicos.exportador_planilha import (
        gravar_planilha_coleta,
        nome_arquivo_coleta,
    )

    db = modulo_database()
    try:
        parlamentares = await asyncio.to_thread(
            db.listar_parlamentares,
            fonte=fonte, uf=uf, partido=partido, termo=termo, limite=limite,
        )
    except db.ErroBancoDados:
        raise _erro_repositorio(db) from None

    arquivo = nome_arquivo_coleta(fonte=fonte or "todas", uf=uf or "todas")
    destino = settings.dir_perfil / arquivo
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        gravar_planilha_coleta(parlamentares, destino)
    except OSError as exc:
        logger.warning("hub: plano de gravação da planilha de coleta falhou: {e}", e=exc)
        raise HTTPException(
            status_code=500,
            detail="Não foi possível gravar a planilha de coleta em disco.",
        ) from exc

    if not baixar:
        return {
            "arquivo": arquivo,
            "total": len(parlamentares),
            "fonte": fonte or "todas",
            "uf": uf or "todas",
        }

    from fastapi.responses import FileResponse

    return FileResponse(
        destino,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=arquivo,
    )