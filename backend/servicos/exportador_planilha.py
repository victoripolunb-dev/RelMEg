"""
Planilha pré-preenchida de coleta — gerador no padrão "Modelo Base".

Gera o .xlsx de COLEÇÃO DE PERFIL (o mesmo gabarito do MODELO BASE que o TSE
usa) a partir dos PARLAMENTARES SALVOS no hub (relmeg_core). As colunas de
identidade (Casa, Nome, Partido, UF) vêm prontas; os campos de coleta manual
(celular, assessoria, contato, e-mail, gabinete, redes, profissão, nascimento,
idade, cor/raça, gênero, escolaridade, estado civil, observação) ficam em
branco para o operador preencher no trabalho de campo — exatamente o papel da
ferramenta original (prefeitura de contato, sem API externa).

Siga do contrato: o cabeçalho, a ordem, as larguras e os estilos vêm de
``modelo_base.gabarito_modelo()`` (contrato do operador ou cópia interna), de
modo que qualquer planilha gerada aqui é estruturalmente idêntica às do TSE.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from servicos.modelo_base import bytes_com_gabarito, gabarito_modelo, gravar_com_gabarito

NOMES_CASA = {
    "camara": "Câmara",
    "senado": "Senado",
    "cldf": "CLDF",
    "dou": "DOU",
    "algo": "ALGO",
    "almg": "ALMG",
    "alesp": "ALESP",
}


def _casa_legislativa(p: Dict[str, Any]) -> str:
    fonte = str(p.get("fonte") or "").strip().lower()
    return NOMES_CASA.get(fonte, fonte.upper() or "—")


def linha_parlamentar(p: Dict[str, Any], tamanho_contrato: int = 25) -> List[Any]:
    """Mapeia um parlamentar salvo no hub para as 25 posições do MODELO BASE.

    Preenche apenas o que o motor já conhece; o restante (contato e perfil)
    fica em branco para coleta manual. Campos de contato recebem "-" como os
    extratos TSE.
    """
    nome = str(
        p.get("nome_urna") or p.get("nome_completo") or ""
    ).strip().upper() or "—"

    linha = [
        _casa_legislativa(p),                        # Casa Legislativa
        nome,                                        # Nome
        str(p.get("partido") or "").strip().upper(), # Partido
        str(p.get("uf") or "").strip().upper(),      # UF
        "",                                          # Eleição/ Reeleição (manual)
        "", "", "", "",                              # FPE, FCS, FPBio, FPEvang (manual)
        "",                                          # Sinergia FPE
        "",                                          # Eixo de Atuação
        "-", "-", "-", "-", "-", "-",                # Celular, Assessoria, Contato, E-mail, Gabinete, Rede
        "", "", "", "", "", "", "",                  # Profissão, Nascimento, Idade, Cor, Gênero, Escolaridade, Est. Civil
        "",                                          # Observação
    ]
    # Alinha ao tamanho exato do contrato (tolera evoluções futuras do modelo).
    if len(linha) != tamanho_contrato:
        linha = (linha + [""] * tamanho_contrato)[:tamanho_contrato]
    return linha


def dataframe_coleta(parlamentares: List[Dict[str, Any]]) -> pd.DataFrame:
    """DataFrame final seguindo fielmente o MODELO BASE (colunas do contrato)."""
    colunas = gabarito_modelo()["colunas"]
    if not parlamentares:
        return pd.DataFrame(columns=colunas)

    df = pd.DataFrame(
        linha_parlamentar(p, len(colunas)) for p in parlamentares
    )
    df.columns = colunas

    # Ordenação estável para leitura em ferramentas de BI (igual ao TSE).
    df = df.sort_values(
        ["Partido", "Nome"],
        key=lambda s: s.fillna("").astype(str).str.lower(),
        na_position="last",
    ).reset_index(drop=True)
    return df


def bytes_planilha_coleta(parlamentares: List[Dict[str, Any]]) -> bytes:
    """Serializa a planilha de coleta (.xlsx em memória) no padrão MODELO BASE."""
    return bytes_com_gabarito(dataframe_coleta(parlamentares))


def gravar_planilha_coleta(
    parlamentares: List[Dict[str, Any]], caminho: Path
) -> Path:
    """Grava a planilha de coleta no disco (padrão MODELO BASE)."""
    gravar_com_gabarito(dataframe_coleta(parlamentares), caminho)
    return caminho


def _token_seguro(valor: Any, maiusculas: bool = False) -> str:
    """Sanitiza um componente de nome de arquivo: só letras, números, hífen e _.

    Neutraliza separadores de caminho (``/``, ``\\``, ``..``) e caracteres
    inválidos que chegariam de ``fonte``/``uf`` fornecidos pelo operador.
    """
    import re

    texto = str(valor or "").strip()
    texto = texto.upper() if maiusculas else texto.lower()
    texto = re.sub(r"[^A-Za-z0-9_-]+", "-", texto)
    return texto.strip("-_") or "todas"


def nome_arquivo_coleta(
    fonte: str = "todas",
    uf: str = "todas",
) -> str:
    """Nome dinâmico: Planilha_Coleta_{casa}_{UF}.xlsx (componentes sanitizados)."""
    regiao = str(uf or "").strip()
    eh_uf = regiao and regiao.lower() != "todas"
    casa = _token_seguro(fonte) if (fonte or "").strip().lower() != "todas" else "todas"
    regiao = _token_seguro(uf, maiusculas=eh_uf)
    return f"Planilha_Coleta_{casa}_{regiao}.xlsx"