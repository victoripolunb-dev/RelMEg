"""
Modelo Base Multiaba (novo formato TSE) — gerador de planilhas .xlsx com 5 abas.

Fonte de fidelidade visual: o arquivo de referência "MODELO BASE MULTIABA"
(cópia de "Modelo base.xlsx" que o operador deixou em RelMeg - Entregas/
Relatórios/TSE). O gabarito é lido em tempo de execução e replicado na saída:
mesma ordem de abas, cabeçalhos, larguras, core de estilos (Montserrat bold
branco sobre 1F4E79, freeze A2, autofilter) e formatos de moeda/datas.

Abas:
    - Candidatos            (37 colunas — perfil completo do candidato)
    - Receitas              (detalhe por lançamento — depende de base fornecida)
    - Despesas Contratadas  (detalhe por lançamento — depende de base fornecida)
    - Despesas Pagas        (detalhe por lançamento — depende de base fornecida)
    - Resumo                (totais, situação das candidaturas e partidos)

Garantias:
    - O arquivo de referência é apenas LEITURA (nunca é sobrescrito).
    - Se o gabarito estiver indisponível, cai em um gabarito de fallback
      programático com as mesmas 5 abas e 37 colunas — a geração não quebra.

Conformidade AGENTS.md: nenhuma coleta externa aqui. Esta camada apenas formata
os candidatos já extraídos (via extrator_tse) e grava o .xlsx sob demanda.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List, Optional

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config import settings

NOME_ABA_CANDIDATOS = "Candidatos"
NOME_ABAS_FINANCEIRAS = ["Receitas", "Despesas Contratadas", "Despesas Pagas"]
NOME_ABA_RESUMO = "Resumo"
ABAS_ESPERADAS = [NOME_ABA_CANDIDATOS, *NOME_ABAS_FINANCEIRAS, NOME_ABA_RESUMO]

# Formatos de número replicados do modelo de referência.
_FMT_MOEDA = "R$ #,##0.00"
_FMT_DATA = "dd/MM/yyyy"

# Outras receitas = total financeiro menos as fontes mapeadas (defensivo).
_CHAVES_FONTES = (
    "fefc", "fundo_partidario", "recursos_partidos",
    "recursos_proprios", "doacoes_pf",
)


# ---------------------------------------------------------------------------
# Gabarito de layout (lido da referência ou fallback)
# ---------------------------------------------------------------------------

def _caminho_modelo() -> Path:
    """Caminho do arquivo MODELO BASE MULTIABA (template portátil no repo)."""
    try:
        preferido = settings.modelo_base_multi_template
        if preferido.exists():
            return preferido
    except Exception:
        pass
    # Fallback: o arquivo original que o operador deixou na pasta de entregas.
    return settings.dir_relatorios / "TSE" / "Modelo base.xlsx"


def caminho_entrega_multi(nome: str) -> Optional[Path]:
    """Pasta de entrega do formato multiaba: <entregas>/Relatórios/TSE.

    Fica junto do 'Modelo base.xlsx' de referência (é onde o operador
    organizou os artefatos do TSE). None em ambientes sem filesystem.
    """
    try:
        pasta = settings.dir_relatorios / "TSE"
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta / nome
    except OSError:
        return None


def _ler_gabarito() -> Optional[Dict[str, Any]]:
    """Lê o arquivo de referência e devolve o gabarito de cada aba."""
    caminho = _caminho_modelo()
    try:
        if not caminho.exists():
            return None
        wb = openpyxl.load_workbook(io.BytesIO(caminho.read_bytes()), data_only=False)
        abas: Dict[str, Dict[str, Any]] = {}
        for nome in NOME_ABAS_FINANCEIRAS + [NOME_ABA_CANDIDATOS, NOME_ABA_RESUMO]:
            if nome not in wb.sheetnames:
                continue
            sh = wb[nome]
            colunas: list = []
            larguras: Dict[int, float] = {}
            moedas: List[int] = []
            for col in range(1, sh.max_column + 1):
                cel = sh.cell(row=1, column=col)
                if cel.value is None:
                    break
                colunas.append(str(cel.value))
                letra = get_column_letter(col)
                dim = sh.column_dimensions.get(letra)
                larguras[col - 1] = round(dim.width, 2) if dim and dim.width else 12.0
                cel_probe = sh.cell(row=2, column=col)
                if cel_probe.number_format in (_FMT_MOEDA, "R$ #,##0.00"):
                    moedas.append(col - 1)
            if not colunas:
                continue
            abas[nome] = {
                "colunas": colunas,
                "larguras": larguras,
                "moedas": moedas,
                "autofilter": sh.auto_filter.ref,
            }
        if NOME_ABA_CANDIDATOS not in abas:
            return None
        return {"origem": str(caminho), "abas": abas}
    except Exception:
        return None


_COLUNAS_FALLBACK_CANDIDATOS = [
    "SQ_CANDIDATO", "Nome Completo", "Nome de Urna", "Nome Social", "Nº de Urna",
    "CPF", "Data de Nascimento", "Idade (data da posse)", "Naturalidade",
    "Gênero", "Grau de Instrução", "Ocupação", "Estado Civil", "Cor/Raça",
    "Nacionalidade", "Partido", "Partido (nome completo)", "Tipo de Agremiação",
    "Federação (sigla)", "Federação (nome)", "Composição da Federação",
    "Coligação", "Composição da Coligação", "Situação da Candidatura (julgamento)",
    "Situação da Candidatura (bruta)", "Situação da Prestação de Contas",
    "Município da Candidatura", "Limite de Gastos (Portaria TSE 449/2026)",
    "Total de Recursos Recebidos", "Fundo Especial de Financiamento de Campanha (FEFC)",
    "Fundo Partidário", "Recursos de Partidos/Doações de Partidos",
    "Recursos Próprios", "Doações de Pessoas Físicas", "Outras Receitas",
    "Total de Despesas Contratadas", "Total de Despesas Pagas",
]

_COLUNAS_FALLBACK_FINANCE = {
    "Receitas": [
        "SQ_CANDIDATO", "Nome de Urna", "Nº", "Data da Receita",
        "Descrição da Receita", "Origem", "Fonte", "Natureza", "Espécie",
        "Doador", "CPF/CNPJ do Doador", "Nº Recibo", "Data Prest. Contas", "Valor",
    ],
    "Despesas Contratadas": [
        "SQ_CANDIDATO", "Nome de Urna", "Nº", "Data da Despesa",
        "Descrição da Despesa", "Fornecedor", "CPF/CNPJ do Fornecedor",
        "Tipo de Fornecedor", "Origem da Despesa", "Tipo de Documento",
        "Nº Documento", "Valor Contratado",
    ],
    "Despesas Pagas": [
        "SQ_CANDIDATO", "Nome de Urna", "Nº", "Data do Pagamento",
        "Descrição da Despesa", "Fonte", "Origem da Despesa", "Natureza",
        "Tipo de Documento", "Nº Documento", "Valor Pago",
    ],
}

_COLUNAS_FALLBACK_RESUMO = ["Indicador", "Valor"]

_LARGURAS_FALLBACK_CANDIDATOS = [
    12.0, 34.0, 30.0, 24.0, 11.0, 16.0, 14.0, 10.0, 22.0, 14.0, 26.0, 34.0,
    18.0, 14.0, 16.0, 10.0, 34.0, 22.0, 22.0, 30.0, 36.0, 30.0, 40.0, 22.0,
    18.0, 14.0, 26.0, 18.0, 16.0, 16.0, 14.0, 16.0, 16.0, 16.0, 16.0, 16.0,
    16.0,
]

_MOEDAS_FALLBACK_CANDIDATOS = list(range(27, 37))  # colunas 28..37 (1-based)


def _gabarito_fallback() -> Dict[str, Any]:
    """Gabarito garantido quando a referência não está acessível."""
    abas = {
        NOME_ABA_CANDIDATOS: {
            "colunas": list(_COLUNAS_FALLBACK_CANDIDATOS),
            "larguras": {i: w for i, w in enumerate(_LARGURAS_FALLBACK_CANDIDATOS)},
            "moedas": list(_MOEDAS_FALLBACK_CANDIDATOS),
            "autofilter": None,
        },
    }
    for nome, colunas in _COLUNAS_FALLBACK_FINANCE.items():
        abas[nome] = {
            "colunas": list(colunas),
            "larguras": {i: 16.0 for i in range(len(colunas))},
            "moedas": [len(colunas) - 1],
            "autofilter": None,
        }
    abas[NOME_ABA_RESUMO] = {
        "colunas": list(_COLUNAS_FALLBACK_RESUMO),
        "larguras": {0: 52.0, 1: 18.0},
        "moedas": [],
        "autofilter": None,
    }
    return {"origem": "fallback (MODELO BASE MULTIABA indisponível)", "abas": abas}


_CACHE_GABARITO: Dict[str, Any] = {"mtime": None, "gabarito": None}


def gabarito_multi() -> Dict[str, Any]:
    """Gabarito canônico (cacheado por mtime) — sempre o modelo vigente."""
    caminho = _caminho_modelo()
    try:
        mtime = caminho.stat().st_mtime if caminho.exists() else None
    except OSError:
        mtime = None
    cache = _CACHE_GABARITO
    if cache["mtime"] != mtime:
        cache["mtime"] = mtime
        cache["gabarito"] = _ler_gabarito()
    gabarito = cache["gabarito"]
    return gabarito if gabarito else _gabarito_fallback()


# ---------------------------------------------------------------------------
# Linhas de dados
# ---------------------------------------------------------------------------

def _texto_ou_marcador(valor: Any, marcador: str = "#NULO") -> str:
    texto = "" if valor is None else str(valor).strip()
    normalizado = texto.upper()
    if (
        not normalizado
        or normalizado in ("NONE", "#NULO", "NAN")
        or normalizado.startswith(("NULL", "NAN"))
    ):
        # "null-null"/"nan" e variantes do TSE não são dados — viram marcador.
        return marcador
    return texto


def _data_ddmm(valor: Any) -> str:
    """Normaliza data para dd/MM/aaaa (defensivo a ISO, timestamps e strings)."""
    texto = str(valor or "").strip()
    if not texto or texto.upper() == "#NULO":
        return "#NULO"
    if "/" in texto and len(texto.split("/")[0]) == 2 and len(texto) == 10:
        return texto
    import datetime

    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.datetime.strptime(texto[:10], formato).strftime("%d/%m/%Y")
        except ValueError:
            continue
    if texto.isdigit() and len(texto) == 13:
        try:
            return datetime.datetime.fromtimestamp(int(texto) / 1000).strftime("%d/%m/%Y")
        except (ValueError, OverflowError, OSError):
            return texto
    return texto


def _idade_na_posse(data_nascimento: Any) -> str:
    from datetime import date

    texto = _data_ddmm(data_nascimento)
    if texto in ("#NULO", "None", ""):
        return "#NULO"
    try:
        d, m, a = texto.split("/")
        nasc = date(int(a), int(m), int(d))
        posse = date(2027, 1, 1)  # data da posse das Eleições 2026
        return str(posse.year - nasc.year - ((posse.month, posse.day) < (nasc.month, nasc.day)))
    except (ValueError, TypeError):
        return "#NULO"


def _linha_candidato(c: Dict[str, Any]) -> List[Any]:
    """Monta uma linha nas 37 posições exatas da aba Candidatos."""
    total_recebido = c.get("total_recebido")
    outras = c.get("outras_receitas")
    if outras is None and total_recebido is not None:
        soma_fontes = sum(
            float(c.get(chave) or 0) for chave in _CHAVES_FONTES
        )
        outras = round(max(float(total_recebido) - soma_fontes, 0.0), 2)

    return [
        _texto_ou_marcador(c.get("id_candidato")),                 # SQ_CANDIDATO
        _texto_ou_marcador(c.get("nome_completo"), "—"),           # Nome Completo
        _texto_ou_marcador(c.get("nome_urna")),                    # Nome de Urna
        _texto_ou_marcador(c.get("nome_social")),                  # Nome Social
        _texto_ou_marcador(c.get("numero"), "-"),                  # Nº de Urna
        _texto_ou_marcador(c.get("cpf")),                          # CPF
        _data_ddmm(c.get("data_nascimento")),                      # Data de Nascimento
        _idade_na_posse(c.get("data_nascimento")),                 # Idade (posse)
        _texto_ou_marcador(c.get("naturalidade")),                 # Naturalidade
        _texto_ou_marcador(c.get("genero")),                       # Gênero
        _texto_ou_marcador(c.get("grau_instrucao")),               # Grau de Instrução
        _texto_ou_marcador(c.get("ocupacao")),                     # Ocupação
        _texto_ou_marcador(c.get("estado_civil")),                 # Estado Civil
        _texto_ou_marcador(c.get("cor_raca")),                     # Cor/Raça
        _texto_ou_marcador(c.get("nacionalidade")),                # Nacionalidade
        _texto_ou_marcador(c.get("partido_sigla")),                # Partido
        _texto_ou_marcador(c.get("partido_nome")),                 # Partido (nome completo)
        _texto_ou_marcador(c.get("tipo_agremiacao")),              # Tipo de Agremiação
        _texto_ou_marcador(c.get("federacao_sigla")),              # Federação (sigla)
        _texto_ou_marcador(c.get("federacao_nome")),               # Federação (nome)
        _texto_ou_marcador(c.get("composicao_federacao")),         # Composição da Federação
        _texto_ou_marcador(c.get("nome_coligacao")),               # Coligação
        _texto_ou_marcador(c.get("composicao_coligacao")),         # Composição da Coligação
        _texto_ou_marcador(c.get("situacao")),                     # Situação (julgamento)
        _texto_ou_marcador(c.get("situacao_bruta")),               # Situação (bruta)
        _texto_ou_marcador(c.get("situacao_prestacao") or "N"),    # Sit. Prestação de Contas
        _texto_ou_marcador(c.get("municipio")),                    # Município
        c.get("limite_gastos"),                                    # Limite de Gastos
        c.get("total_recebido"),                                   # Total de Recursos Recebidos
        c.get("fefc"),                                             # FEFC
        c.get("fundo_partidario"),                                 # Fundo Partidário
        c.get("recursos_partidos"),                                # Recursos de Partidos
        c.get("recursos_proprios"),                                # Recursos Próprios
        c.get("doacoes_pf"),                                       # Doações PF
        outras,                                                    # Outras Receitas
        c.get("total_despesas_contratadas"),                       # Desp. Contratadas
        c.get("total_despesas_pagas"),                             # Desp. Pagas
    ]


def _linhas_resumo(
    candidatos: List[Dict[str, Any]], ano: int, uf: str, cargo_nome: str
) -> List[List[Any]]:
    """Monta as linhas da aba Resumo (indicador + valor)."""
    total = len(candidatos)
    cargo_legivel = str(cargo_nome or "").replace("_", " ")
    titulo = f"Total de Candidatos ({cargo_legivel} {uf.upper()} {ano})"

    por_situacao: Dict[str, int] = {}
    por_partido: Dict[str, int] = {}
    for c in candidatos:
        sit = str(c.get("situacao") or "—").strip().upper()
        por_situacao[sit] = por_situacao.get(sit, 0) + 1
        partido = str(c.get("partido_sigla") or "—").strip().upper()
        por_partido[partido] = por_partido.get(partido, 0) + 1

    linhas: List[List[Any]] = [[titulo, total]]
    for sit in (
        "DEFERIDO",
        "AGUARDANDO JULGAMENTO",
        "RENÚNCIA",
        "INDEFERIDO",
        "INDEFERIDO EM PRAZO RECURSAL OU COM RECURSO",
    ):
        linhas.append([f"Candidatos: {sit}", por_situacao.get(sit, 0)])
    for sit, qtd in sorted(
        (s for s in por_situacao.items() if s[0] not in (
            "DEFERIDO", "AGUARDANDO JULGAMENTO", "RENÚNCIA",
            "INDEFERIDO", "INDEFERIDO EM PRAZO RECURSAL OU COM RECURSO",
        )),
        key=lambda x: (-x[1], x[0]),
    ):
        linhas.append([f"Candidatos: {sit}", qtd])

    linhas.append([f"Total de Partidos/Federações representadas", len(por_partido)])
    linhas.append([])  # linha em branco separadora
    linhas.append(["Partido", "Candidatos"])
    for partido, qtd in sorted(por_partido.items(), key=lambda x: (-x[1], x[0])):
        linhas.append([partido, qtd])
    return linhas


# ---------------------------------------------------------------------------
# Aplicação do estilo + gravação
# ---------------------------------------------------------------------------

def _estilo_cabecalho(sh) -> None:
    for cel in sh[1]:
        cel.font = Font(name="Montserrat", size=11, bold=True, color="FFFFFFFF")
        cel.fill = PatternFill(fill_type="solid", fgColor="001F4E79")
        cel.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


_THIN = Side(style="thin")


def _estilo_dados(sh, larguras, moedas, total_linhas, colunas) -> None:
    _BORDA = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
    for i, largura in larguras.items():
        try:
            sh.column_dimensions[get_column_letter(i + 1)].width = largura
        except Exception:
            pass
    for linha in range(2, total_linhas + 1):
        zebra = linha % 2 == 0
        for col in range(1, len(colunas) + 1):
            cel = sh.cell(row=linha, column=col)
            cel.font = Font(name="Montserrat", size=10, color="FF333333")
            cel.border = _BORDA
            if zebra:
                cel.fill = PatternFill(fill_type="solid", fgColor="00F2F7FB")
            if col - 1 in moedas:
                cel.number_format = _FMT_MOEDA
                cel.alignment = Alignment(horizontal="right")
    sh.freeze_panes = "A2"


def _preencher_aba(sh, gab_aba: Dict[str, Any], linhas: List[List[Any]]) -> None:
    colunas = gab_aba["colunas"]
    for col, nome in enumerate(colunas, start=1):
        sh.cell(row=1, column=col, value=nome)
    for r_idx, linha in enumerate(linhas, start=2):
        for c_idx, valor in enumerate(linha[: len(colunas)], start=1):
            sh.cell(row=r_idx, column=c_idx, value=valor)
    _estilo_cabecalho(sh)
    _estilo_dados(sh, gab_aba.get("larguras", {}), gab_aba.get("moedas", []),
                  total_linhas=1 + len(linhas), colunas=colunas)
    if linhas:
        ultima = get_column_letter(len(colunas))
        try:
            sh.auto_filter.ref = f"A1:{ultima}{1 + len(linhas)}"
        except Exception:
            pass


def construir_workbook(
    candidatos: List[Dict[str, Any]],
    ano: int,
    uf: str,
    codigo_cargo: int,
    cargo_nome: Optional[str] = None,
) -> bytes:
    """Monta o .xlsx multiaba em memória a partir dos candidatos extraídos.

    As abas financeiras (Receitas/Despesas) são criadas com cabeçalho e ficam
    vazias quando não há base de lançamentos fornecida (a API do TSE não expõe
    o detalhe em linha — somente totais consolidados na aba Candidatos).
    """
    import extrator_tse

    cargo = cargo_nome or extrator_tse._nome_cargo(codigo_cargo)
    gab = gabarito_multi()
    abas = gab["abas"]

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # 1) Candidatos
    sh = wb.create_sheet(NOME_ABA_CANDIDATOS)
    linhas_cand = [_linha_candidato(c) for c in candidatos]
    _preencher_aba(sh, abas[NOME_ABA_CANDIDATOS], linhas_cand)

    # 2) Abas financeiras (cabeçalho replicado; sem dados sem base fornecida)
    for nome in NOME_ABAS_FINANCEIRAS:
        gab_aba = abas.get(nome)
        if not gab_aba:
            continue
        sh = wb.create_sheet(nome)
        _preencher_aba(sh, gab_aba, [])

    # 3) Resumo
    sh = wb.create_sheet(NOME_ABA_RESUMO)
    _preencher_aba(sh, abas[NOME_ABA_RESUMO], _linhas_resumo(candidatos, ano, uf, cargo))

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def gravar_arquivo_multi(
    candidatos: List[Dict[str, Any]],
    ano: int,
    uf: str,
    codigo_cargo: int,
) -> Optional[str]:
    """Grava o .xlsx multiaba em RelMeg - Entregas/Relatórios/TSE (ou None em
    deploy sem filesystem)."""
    import extrator_tse

    nome = (
        f"TSE_{ano}_{uf.strip().upper()}_{extrator_tse._nome_cargo(codigo_cargo)}"
        f"MULTIABA.xlsx"
    )
    destino = caminho_entrega_multi(nome)
    if destino is None:
        return None
    conteudo = construir_workbook(candidatos, ano, uf, codigo_cargo)
    destino.write_bytes(conteudo)
    return str(destino)