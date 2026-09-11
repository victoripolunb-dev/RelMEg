"""Helpers compartilhados entre conectores, routers e utilitários do RelMeg.

Centraliza funções genéricas que se repetiam em múltiplos módulos (DRY).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional


def para_int(valor: Any, default: Optional[int] = 0) -> Optional[int]:
    """Extrai o primeiro inteiro de um valor (formato numérico robusto)."""
    m = re.search(r"\d+", str(valor or ""))
    if not m:
        return default
    try:
        return int(m.group(0))
    except (TypeError, ValueError):
        return default


def parse_date_iso(valor: Any) -> Optional[date]:
    """Normaliza data/hora ISO ou brasileira para ``date`` (ou None se inválida).

    Aceita formatos ISO (``2026-09-10``, ``2026-09-10T14:30:00Z``) e
    brasileiros (``10/09/2026``).
    """
    if not valor:
        return None
    texto = str(valor).strip()
    # Tenta ISO primeiro (formato predominante nas APIs)
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        pass
    # Fallback: formato brasileiro dd/mm/aaaa
    try:
        return datetime.strptime(texto[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def listar(valor: Any) -> list:
    """Garante uma lista: se ``valor`` já for lista, retorna; se dict, envolve; senão, lista vazia."""
    if isinstance(valor, list):
        return valor
    if isinstance(valor, dict):
        return [valor]
    return []


def modulo_database():
    """Import tardio de ``database`` (módulo-irmão do app, fora do pacote).

    Evita import circular: ``relmeg.core`` não importa ``database`` no topo;
    quem precisar (routers, orquestrador, conectores) chama ``modulo_database()``.
    """
    import database  # noqa: PLC0415  (import local para isolar dependência)

    return database
