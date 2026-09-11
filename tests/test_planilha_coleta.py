"""
Testes do gerador de PLANILHA DE COLETA (exportador_planilha) — contrato Modelo Base.

Sem rede: só arquivos locais (gabarito real do contrato). Valida o mapeamento
parlamentar→25 colunas, o alinhamento entre o arquivo do operador e a cópia
interna do repositório, e a serialização .xlsx resultante.
"""
import io

import openpyxl
import pandas as pd

from config import settings
from exportador_planilha import (
    linha_parlamentar,
    dataframe_coleta,
    bytes_planilha_coleta,
    gravar_planilha_coleta,
    nome_arquivo_coleta,
    NOMES_CASA,
)
from modelo_base import (
    COLUNAS_MODELO_BASE,
    MODELO_BASE_OPERADOR,
    gabarito_modelo,
)


# ---------------------------------------------------------------------------
# Mapeamento de linha (contrato de 25 colunas)
# ---------------------------------------------------------------------------

def test_linha_parlamentar_preenche_identidade_e_deixa_coleta_em_branco():
    p = {
        "fonte": "camara",
        "nome_urna": "FULANO SILVA",
        "nome_completo": "FULANO DE TAL DA SILVA",
        "partido": "pl",
        "uf": "sp",
    }
    linha = linha_parlamentar(p)
    assert len(linha) == len(COLUNAS_MODELO_BASE) == 25
    assert linha[0] == "Câmara"          # Casa Legislativa
    assert linha[1] == "FULANO SILVA"    # Nome (urna, em MAIÚSCULAS)
    assert linha[2] == "PL"              # Partido
    assert linha[3] == "SP"              # UF
    assert linha[11] == "-"              # Celular
    assert linha[14] == "-"              # E-mail
    assert linha[16] == "-"              # Rede Social
    assert linha[17] == ""               # Profissão (manual)
    assert linha[18] == ""               # Data de Nascimento (manual)
    assert linha[24] == ""               # Observação (manual)


def test_linha_parlamentar_nome_usa_urna_quando_diferente():
    linha = linha_parlamentar({"fonte": "senado", "nome_completo": "MARIA SOUZA"})
    assert linha[1] == "MARIA SOUZA"     # sem nome_urna → cai para nome_completo


def test_linha_parlamentar_nome_fonte_sem_nome_vira_traco():
    linha = linha_parlamentar({})
    assert linha[1] == "—"


def test_linha_parlamentar_casa_desconhecida_por_fonte():
    linha = linha_parlamentar({"fonte": "algo", "nome_completo": "X"})
    assert linha[0] == "ALGO"            # NOMES_CASA cobre as fontes canônicas


def test_linha_parlamentar_alinhada_ao_contrato_se_mudar():
    # Se o contrato mudar de tamanho, a linha se adapta sem estourar.
    linha = linha_parlamentar({}, tamanho_contrato=27)
    assert len(linha) == 27


# ---------------------------------------------------------------------------
# DataFrame final
# ---------------------------------------------------------------------------

def test_dataframe_coleta_usa_colunas_do_contrato():
    registros = [
        {"fonte": "camara", "nome_completo": "ANA", "partido": "PT", "uf": "df"},
        {"fonte": "camara", "nome_urna": "BIA", "partido": "PL", "uf": "rj"},
    ]
    df = dataframe_coleta(registros)
    assert list(df.columns) == gabarito_modelo()["colunas"]
    assert list(df.columns) == COLUNAS_MODELO_BASE
    assert len(df) == 2
    assert list(df["Partido"]) == ["PL", "PT"]   # ordenado por Partido
    assert df.loc[1, "Casa Legislativa"] == "Câmara"


def test_dataframe_coleta_vazio_manter_colunas():
    df = dataframe_coleta([])
    assert list(df.columns) == COLUNAS_MODELO_BASE
    assert df.empty


# ---------------------------------------------------------------------------
# Serialização e arquivo
# ---------------------------------------------------------------------------

def test_bytes_planilha_coleta_xlsx_valido():
    conteudo = bytes_planilha_coleta([{"fonte": "senado", "nome_completo": "X", "partido": "PSD", "uf": "GO"}])
    assert conteudo[:2] == b"PK"  # zip (xlsx)
    wb = openpyxl.load_workbook(io.BytesIO(conteudo))
    sh = wb["Candidatos"]
    assert sh.cell(1, 2).value == "Nome"   # cabeçalho do contrato
    assert sh.cell(2, 1).value == "Senado"


def test_gravar_planilha_coleta_escreve_em_disco(tmp_path):
    destino = tmp_path / "coleta.xlsx"
    gravar_planilha_coleta(
        [{"fonte": "cldf", "nome_completo": "DISTRITAL", "partido": "PSB", "uf": "DF"}],
        destino,
    )
    assert destino.exists() and destino.stat().st_size > 1000
    wb = openpyxl.load_workbook(str(destino))
    assert wb["Candidatos"].cell(2, 2).value == "DISTRITAL"


def test_nome_arquivo_coleta():
    assert nome_arquivo_coleta("camara", "SP") == "Planilha_Coleta_camara_SP.xlsx"
    assert nome_arquivo_coleta() == "Planilha_Coleta_todas_todas.xlsx"


# ---------------------------------------------------------------------------
# CONTRATO: arquivo do operador × cópia interna alinhados
# ---------------------------------------------------------------------------

def test_nomes_casa_cobrem_fontes_registradas():
    assert NOMES_CASA.keys() >= {"camara", "senado", "cldf"}


def test_contrato_operador_e_copia_interna_alinhados():
    """O gabarito do operador (contrato real) e a cópia interna têm o mesmo cabeçalho."""
    gab = gabarito_modelo()
    assert gab["origem"] in {str(MODELO_BASE_OPERADOR), str(settings.modelo_base_template)}
    assert gab["colunas"] == COLUNAS_MODELO_BASE

    if MODELO_BASE_OPERADOR.exists():
        wb = openpyxl.load_workbook(io.BytesIO(MODELO_BASE_OPERADOR.read_bytes()))
        sh = wb["Câmara"] if "Câmara" in wb.sheetnames else wb[wb.sheetnames[0]]
        cabecalho_operador = [
            sh.cell(1, c).value for c in range(1, sh.max_column + 1)
            if sh.cell(1, c).value is not None
        ]
        assert cabecalho_operador == COLUNAS_MODELO_BASE
        assert len(cabecalho_operador) == 25

        # Contrato é a ÚNICA versão: a cópia interna deve ser byte a byte igual.
        interno = settings.modelo_base_template
        if interno.exists():
            assert MODELO_BASE_OPERADOR.read_bytes() == interno.read_bytes()


def test_contrato_disponivel_ao_menos_uma_fonte():
    from modelo_base import contrato_disponivel

    assert contrato_disponivel()