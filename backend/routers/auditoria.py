"""
Auditoria e histórico de execuções do RelMeg.

Centraliza a visibilidade do motor: histórico consolidado das extrações do TSE
(execuções em segundo plano), resumo de métricas operacionais (sucesso, falhas,
tempo médio) e o log estruturado de eventos (Family Talks, Modelo Base, PDFs e
falhas tratadas).

Consulta: GET /api/execucoes/historico  (alias: GET /api/execucoes/auditoria)
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Query
from starlette.requests import Request

import database
from rate_limit import limiter

router = APIRouter(prefix="/api/execucoes", tags=["Auditoria"])


def _somente_nome(caminho: str | None) -> str | None:
    """A3 — expõe apenas o nome do arquivo no JSON (nunca o caminho absoluto)."""
    if not caminho:
        return caminho
    return Path(caminho).name


@router.get("/historico")
@router.get("/auditoria")
@limiter.limit("20/minute")
async def historico_execucoes(
    request: Request,
    limite: int = Query(50, ge=1, le=500, description="Quantidade de execuções recentes"),
    eventos: int = Query(30, ge=0, le=200, description="Quantidade de eventos estruturados recentes"),
):
    """Panorama das execuções + resumo de métricas + log de eventos."""
    historico = await asyncio.to_thread(database.historico_execucoes_tse, limite)
    for execucao in historico:
        if "caminho_arquivo" in execucao:
            execucao["caminho_arquivo"] = _somente_nome(execucao["caminho_arquivo"])
    resumo = await asyncio.to_thread(database.resumo_metricas_tse)
    eventos_recentes = (
        await asyncio.to_thread(database.listar_eventos, eventos)
        if eventos > 0 else []
    )
    return {
        "total_retornado": len(historico),
        "resumo_metricas": resumo,
        "execucoes": historico,
        "eventos_recentes": eventos_recentes,
    }