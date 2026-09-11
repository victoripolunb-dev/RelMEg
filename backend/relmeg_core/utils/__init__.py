"""Utilitários internos do motor (helpers compartilhados + wrapper lazy do Scrapling)."""
from relmeg_core.utils.helpers import listar, para_int, parse_date_iso
from relmeg_core.utils.scrapling_engine import (
    extrair_html_seguro,
    extrair_html_simples,
)

__all__ = [
    "extrair_html_seguro",
    "extrair_html_simples",
    "listar",
    "para_int",
    "parse_date_iso",
]
