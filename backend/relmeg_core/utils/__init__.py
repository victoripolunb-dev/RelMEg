"""Utilitários internos do motor (hoje: wrapper lazy do Scrapling)."""
from relmeg_core.utils.scrapling_engine import (
    extrair_html_seguro,
    extrair_html_simples,
)

__all__ = ["extrair_html_seguro", "extrair_html_simples"]