# -*- coding: utf-8 -*-
"""
Gera as entregas do relatório de NOVAS PROPOSIÇÕES — Setor de Educação
(01 a 30/09/2026) a partir do JSON coletado por ``coletar_educacao.py``.

Entregas (pasta canônica de entregas, AGENTS.md):
    1) .docx — relatório executivo no modelo visual do relatório de DOU
       (Montserrat, títulos em azul 1F4E79, links de destaque em negrito
       vermelho) com a estrutura do clipping Family Talks: banner de período,
       leitura do período, blocos temáticos e itens numerados.
    2) .xlsx — planilha analítica multiaba (Proposições, Por tema, Por casa,
       Metodologia) com filtro automático.

Execução sob demanda (nunca automática — AGENTS.md):
    .venv\\Scripts\\python.exe gerar_relatorio_educacao.py
"""
from __future__ import annotations

import json
import sys
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import settings  # noqa: E402

FONTE_JSON = (settings.dir_entregas / settings.subdir_novas_proposicoes
              / "proposicoes_educacao_setembro_2026.json")
JANELA_INI, JANELA_FIM = date(2026, 9, 1), date(2026, 9, 30)
JANELA_BR = "01/09/2026 a 30/09/2026"
ARQ_DOCX = (settings.dir_entregas / settings.subdir_novas_proposicoes
            / "relatorio_novas_proposicoes_educacao_setembro_2026.docx")
ARQ_XLSX = (settings.dir_entregas / settings.subdir_novas_proposicoes
            / "planilha_novas_proposicoes_educacao_setembro_2026.xlsx")

# Ordem dos blocos temáticos no relatório (do mais específico/urgente ao geral).
ORDEM_TEMAS = [
    "Educação Infantil",
    "Ensino Fundamental",
    "Ensino Médio",
    "Ensino Superior",
    "Educação Profissional e Técnica",
    "EaD — Educação a Distância",
    "Financiamento e Assistência Estudantil",
    "Avaliação e Ingresso",
    "Regulação e Gestão do MEC",
    "Educação Especial e Inclusão",
    "Educação Privada",
    "Formação Médica",
    "Profissionais da Educação",
    "Tecnologia e Inovação na Educação",
    "Continuidade Educacional em Emergências",
    "Outros temas de educação",
]

# Palavras-chave originais do pedido — usadas no relatório (metodologia).
TERMOS_PEDIDO = [
    "Educação", "Ministério da Educação", "MEC", "Ensino superior",
    "Ensino básico", "Ensino médio", "Ensino privado", "educação particular",
    "EaD", "ensino a distância", "Fies", "Prouni", "Enem", "Enamed", "Fundeb",
    "Universidade", "Faculdade", "Curso superior", "Curso técnico",
    "Educação profissional", "INEP", "Cotas", "Escolas particulares",
    "Colégios", "Medicina", "cursos de medicina",
]

CZ = RGBColor(0x33, 0x33, 0x33)
CINZA = RGBColor(0x66, 0x66, 0x66)
AZUL = RGBColor(0x1F, 0x4E, 0x79)
VERMELHO = RGBColor(0xFF, 0x00, 0x00)
FONTE = "Montserrat"

NOMES_CASA = {"camara": "Câmara dos Deputados", "senado": "Senado Federal"}


# ---------------------------------------------------------------------------
# Normalização de texto / rótulos
# ---------------------------------------------------------------------------

def _norm(texto: Any) -> str:
    texto = str(texto or "").lower()
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii")


def _data_br(valor: Any) -> str:
    try:
        d = date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        return "—"
    return d.strftime("%d/%m/%Y")


def _sigla(item: Dict[str, Any]) -> str:
    return f"{item.get('sigla_tipo') or ''} {item.get('numero')}/{item.get('ano')}".strip()


def _autor(item: Dict[str, Any]) -> str:
    autor = str(item.get("autor") or "").strip()
    if not autor or autor.lower() in {"camara dos deputados"}:
        return "Câmara dos Deputados" if not autor else autor
    return autor


def situacao_curta(valor: Any) -> str:
    """Normaliza a última movimentação do Senado (texto longo) em rótulo curto."""
    texto = " ".join(str(valor or "").split())
    if not texto:
        return "Indisponível"
    if texto.lower().startswith("autuado"):
        return "Autuado — publicado, aguardando comissão"
    if "vai à publicação" in texto.lower():
        return "Autuado — publicado, aguardando comissão"
    if len(texto) > 120:
        return texto[:117].rstrip() + "…"
    return texto


def _relator(item: Dict[str, Any]) -> str:
    """Relator só é informativo quando difere da autoria.

    Em proposições recém-apresentadas o despacho ainda não designou relator e o
    conector devolve o próprio autor nesse campo — reportá-lo seria enganoso.
    """
    relator = str(item.get("relator") or "").strip()
    autor = str(item.get("autor") or "").strip()
    if not relator or (autor and relator.lower() == autor.lower()):
        return ""
    return relator


def _ementa(item: Dict[str, Any]) -> str:
    return " ".join(str(item.get("ementa") or "").split())


# ---------------------------------------------------------------------------
# Estilo DOCX (idêntico ao relatório de DOU)
# ---------------------------------------------------------------------------

def normal(doc: Document) -> None:
    st = doc.styles["Normal"]
    st.font.name = FONTE
    st.font.size = Pt(10)
    st.font.color.rgb = CZ
    rpr = st.element.get_or_add_rPr()
    rpr.rFonts.set(qn("w:eastAsia"), FONTE)
    pf = st.paragraph_format
    pf.line_spacing = 1.15
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def para(doc: Document, before=None, after=0, align=None):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    if before is not None:
        pf.space_before = before
    pf.space_after = after
    p.alignment = align if align is not None else WD_ALIGN_PARAGRAPH.JUSTIFY
    return p


def run(p, texto: str, size=10, bold=False, color=CZ, italic=False):
    r = p.add_run(texto)
    r.font.name = FONTE
    r._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONTE)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    return r


def hyperlink(p, url: str, texto: str, size=11, bold=True):
    part = p.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hl = p._p.makeelement(qn("w:hyperlink"), {qn("r:id"): r_id})
    r = p._p.makeelement(qn("w:r"), {})
    rpr = r.makeelement(qn("w:rPr"), {})
    rf = rpr.makeelement(qn("w:rFonts"), {})
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia"):
        rf.set(qn(attr), FONTE)
    rpr.append(rf)
    if bold:
        rpr.append(rpr.makeelement(qn("w:b"), {}))
    rpr.append(rpr.makeelement(qn("w:sz"), {qn("w:val"): str(size * 2)}))
    rpr.append(rpr.makeelement(qn("w:color"), {qn("w:val"): "FF0000"}))
    r.append(rpr)
    t = r.makeelement(qn("w:t"), {})
    t.text = texto
    r.append(t)
    hl.append(r)
    p._p.append(hl)


def secao_header(doc: Document, titulo: str) -> None:
    p = para(doc, before=152400, after=50800)
    run(p, titulo, 12, bold=True, color=AZUL)


def secao_topico(doc: Document, titulo: str) -> None:
    p = para(doc, before=152400, after=50800)
    run(p, titulo, 14, bold=True, color=AZUL)


def celula(cell, texto: str, size=9, bold=False, color=CZ):
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    if texto:
        run(p, texto, size, bold=bold, color=color)


def tabela(doc: Document, cabecalho: List[str], linhas: List[List[str]],
           link_col: Optional[int] = None,
           link_rotulo: str = "Abrir ficha") -> None:
    tb = doc.add_table(rows=1, cols=len(cabecalho))
    tb.style = "Table Grid"
    for i, nome in enumerate(cabecalho):
        celula(tb.rows[0].cells[i], nome, size=9, bold=True)
    for linha in linhas:
        row = tb.add_row()
        for i, valor in enumerate(linha):
            if i == link_col and valor.startswith("http"):
                hyperlink(row.cells[i].paragraphs[0], valor, link_rotulo, size=9)
            else:
                celula(row.cells[i], valor, size=9)


# ---------------------------------------------------------------------------
# Blocos de conteúdo
# ---------------------------------------------------------------------------

def ficha(doc: Document, indice: int, item: Dict[str, Any]) -> None:
    """Item detalhado no padrão do relatório de DOU."""
    p = para(doc, before=101600, after=25400)
    run(p, f"{indice}. ", 11, bold=True, color=AZUL)
    hyperlink(p, item["url"], _sigla(item), size=11)

    p1 = para(doc)
    run(p1, "Casa: ", 10, bold=True)
    run(p1, NOMES_CASA.get(item["fonte"], item["fonte"]), 10)

    p2 = para(doc)
    run(p2, "Apresentação: ", 10, bold=True)
    run(p2, _data_br(item["data_apresentacao"]), 10)

    p3 = para(doc)
    run(p3, "Autoria: ", 10, bold=True)
    run(p3, _autor(item), 10)
    if item.get("coautores"):
        run(p3, f" (coautoria: {', '.join(item['coautores'])})", 10, color=CINZA)

    p4 = para(doc)
    run(p4, "Tema: ", 10, bold=True)
    run(p4, item.get("subtema") or "—", 10, color=AZUL)

    p5 = para(doc)
    run(p5, "Situação: ", 10, bold=True)
    run(p5, situacao_curta(item.get("situacao")), 10, color=CINZA)
    if item.get("comissao_atual"):
        run(p5, f" · Comissão/Órgão: {item['comissao_atual']}", 10, color=CINZA)
    relator = _relator(item)
    if relator:
        run(p5, f" · Relator: {relator}", 10, color=CINZA)

    p6 = para(doc)
    run(p6, "Ementa: ", 10, bold=True)
    run(p6, _ementa(item), 10)


def ler_periodo(itens: List[Dict[str, Any]]) -> List[Tuple[str, List[str]]]:
    """Blocos de leitura do período (3 a 5 frases por tema)."""
    por_tema: Dict[str, List[Dict[str, Any]]] = {}
    for i in itens:
        por_tema.setdefault(i.get("subtema") or "Outros temas de educação", []).append(i)

    textos = {
        "Educação Infantil": (
            "A educação infantil está entre os temas com maior densidade do "
            "período, com {n} proposições — todas concentradas na ampliação de "
            "oferta e no acolhimento de famílias em situação de vulnerabilidade. "
            "Destaca-se o Programa Auxílio-Creche Primeira Infância (Aline Gurgel), "
            "que cria apoio temporário a famílias de baixa renda sem vaga na rede "
            "pública, e o PL de prioridade de matrícula para filhos de mulheres em "
            "situação de violência doméstica e familiar."
        ),
        "Ensino Fundamental": (
            "O ciclo básico recebeu {n} proposições, com foco em dois eixos: o "
            "dimensionamento das turmas e a educação financeira e controle social "
            "como finalidade da educação básica. O PL 5472/2026 cria o Índice "
            "Nacional de Superlotação de Turmas e fixa limites máximos de "
            "estudantes por turma; o PL 5343/2026 disciplina a divulgação de "
            "livros e materiais escolares."
        ),
        "Ensino Médio": (
            "O ensino médio público aparece com {n} proposição, voltada à "
            "universalização do incentivo financeiro-educacional do Pé-de-Meia e à "
            "periodicidade de atualização dos valores — item que se soma à "
            "extensão do mesmo programa ao ensino superior (PL 5300/2026)."
        ),
        "Ensino Superior": (
            "A rede de ensino superior reúne {n} proposições. O eixo dominante é "
            "a reestruturação institucional: a criação da Universidade Federal da "
            "Fronteira Norte e a transformação dos CEFET de Minas Gerais e do Rio "
            "de Janeiro em universidades de ciência e inovação. Observar que as "
            "proposições do Senado com autoria da Câmara dos Deputados indicam "
            "proposições originárias no outro Órgão, já em curso na Casa revisora."
        ),
        "Educação Profissional e Técnica": (
            "A educação profissional e tecnológica aparece com {n} proposição, que "
            "incorpora a dimensão territorial à oferta do Pronatec e a articula com "
            "as políticas de geração de trabalho, emprego e renda."
        ),
        "EaD — Educação a Distância": (
            "A Política Nacional de Educação Digital é alterada por {n} proposição "
            "para garantir acessibilidade digital e tecnologia assistiva a "
            "estudantes com deficiência — eixo de inclusão que se sobrepõe ao "
            "digital."
        ),
        "Financiamento e Assistência Estudantil": (
            "A assistência estudantil concentra {n} proposições e forma o principal "
            "bloco financeiro do período: a criação do Programa Pé-de-Meia "
            "Universitário, com incentivos de matrícula, permanência e conclusão na "
            "modalidade de poupança, e a universalização do Pé-de-Meia no ensino "
            "médio público."
        ),
        "Avaliação e Ingresso": (
            "O eixo de avaliação e ingresso não teve proposição própria no "
            "período; as proposições de regulação abaixo endereçam o mesmo campo "
            "(supervisão do ensino superior)."
        ),
        "Regulação e Gestão do MEC": (
            "A regulação do setor avança com {n} proposição que autoriza a criação "
            "do INSAES, reforçando a arquitetura de supervisão do ensino superior."
        ),
        "Continuidade Educacional em Emergências": (
            "A continuidade do ensino em situações de exceção reúne {n} proposições: "
            "diretrizes para a proteção da comunidade escolar e a continuidade das "
            "atividades durante eventos climáticos extremos, e o Passaporte "
            "Educacional de Emergência, que assegura matrícula, documentação e "
            "continuidade da aprendizagem em emergências ou calamidade pública."
        ),
    }
    saida = []
    for tema, bloco in textos.items():
        grupo = por_tema.get(tema) or []
        if not grupo:
            continue
        saida.append((tema, [bloco.format(n=len(grupo))]))
    return saida


def gerar_docx(itens: List[Dict[str, Any]], total_bruto: int) -> Path:
    doc = Document()
    normal(doc)
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = 1143000
        sec.top_margin = sec.bottom_margin = 914400

    por_casa = Counter(i["fonte"] for i in itens)
    por_tema = Counter(i.get("subtema") or "Outros temas de educação" for i in itens)

    # ---- Cabeçalho (banner de período no estilo Family Talks) ----
    p = para(doc, before=127000, after=76200, align=WD_ALIGN_PARAGRAPH.CENTER)
    run(p, "Novas proposições — Setor de Educação", 16, bold=True, color=CZ)
    p = para(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    run(p, f"Período: {JANELA_BR} · Câmara dos Deputados e Senado Federal", 11,
        bold=True, color=AZUL)
    p = para(doc, after=127000)
    run(p, "Relatório executivo das proposições apresentadas no Congresso "
           "relevantes ao setor de Educação, com detalhamento por tema, autoria, "
           "situação de tramitação e link da proposição original.", 10, color=CINZA)

    # ---- Metodologia ----
    secao_header(doc, "Metodologia")
    p = para(doc)
    run(p, f"- Período analisado: {JANELA_BR} (data de apresentação das "
           f"proposições).", 9)
    p = para(doc)
    run(p, f"- Fontes: Câmara dos Deputados (API de Dados Abertos) e Senado "
           f"Federal (API de Dados Abertos), ambas consultadas sob demanda.", 9)
    p = para(doc)
    run(p, "- Termos de interesse: " + ", ".join(TERMOS_PEDIDO) + ".", 9)
    p = para(doc)
    run(p, f"- Método de varredura: em vez de buscas por palavra-chave (que "
           f"limitariam o resultado aos 100 itens mais recentes e perderiam "
           f"proposições do mês), varremos o período inteiro por sigla "
           f"(PL, PLP, PEC, PDC, MPV na Câmara; PL, PLP, PEC, PLN, PDL no "
           f"Senado) e aplicamos localmente a matriz de relevância do setor "
           f"Educação sobre a ementa de cada proposição. Foram varridas "
           f"{total_bruto} proposições no período; {len(itens)} são do setor.", 9)
    p = para(doc)
    run(p, "- Situação de tramitação obtida da última movimentação registrada em "
           "cada Casa na data desta extração.", 9)

    # ---- Resumo ----
    secao_header(doc, "Resumo do período")
    linhas_resumo = [
        [NOMES_CASA.get(c, c), str(n)]
        for c, n in por_casa.most_common()
    ]
    tabela(doc, ["Casa legislativa", "Proposições"], linhas_resumo)
    p = para(doc, before=101600, after=25400)
    run(p, f"Total de proposições do setor Educação no período: ", 10)
    run(p, f"{len(itens)}", 10, bold=True, color=AZUL)
    run(p, f" ({NOMES_CASA['camara']}: {por_casa['camara']} · "
           f"{NOMES_CASA['senado']}: {por_casa['senado']}).", 10)

    secao_header(doc, "Distribuição por tema")
    linhas_tema = [
        [tema, str(n)]
        for tema, n in sorted(por_tema.items(), key=lambda x: (-x[1], x[0]))
    ]
    tabela(doc, ["Tema", "Proposições"], linhas_tema)

    # ---- Leitura do período ----
    secao_header(doc, "Leitura do período")
    for tema, paragrafos in ler_periodo(itens):
        p = para(doc, before=76200)
        run(p, f"{tema}. ", 10, bold=True, color=AZUL)
        run(p, paragrafos[0], 10)

    # ---- Visão compacta (índice) ----
    secao_header(doc, "Índice das proposições")
    linhas_indice = [
        [_sigla(i), _data_br(i["data_apresentacao"]),
         NOMES_CASA.get(i["fonte"], i["fonte"]),
         i.get("subtema") or "—", i["url"]]
        for i in itens
    ]
    tabela(doc, ["Proposição", "Apresentação", "Casa", "Tema", "Link"],
           linhas_indice, link_col=4)

    # ---- Detalhamento por tema ----
    secao_topico(doc, "Detalhamento por tema")
    por_tema_itens: Dict[str, List[Dict[str, Any]]] = {}
    for i in itens:
        por_tema_itens.setdefault(i.get("subtema") or "Outros temas de educação",
                                  []).append(i)

    indice = 0
    for tema in ORDEM_TEMAS:
        grupo = por_tema_itens.get(tema)
        if not grupo:
            continue
        secao_header(doc, f"{tema} ({len(grupo)})")
        for item in sorted(grupo, key=lambda x: (x["data_apresentacao"],
                                                 int(x.get("numero") or 0))):
            indice += 1
            ficha(doc, indice, item)

    # ---- Rodapé ----
    p = para(doc, before=152400)
    run(p, f"— Gerado por varredura sob demanda do RelMeg em {date.today():%d/%m/%Y}. "
           f"Dados completos em {FONTE_JSON.name} e planilha de apoio "
           f"{ARQ_XLSX.name}.", 8, color=CINZA)

    ARQ_DOCX.parent.mkdir(parents=True, exist_ok=True)
    doc.save(ARQ_DOCX)
    return ARQ_DOCX


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

_THIN = Side(style="thin")
_BORDA = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
COLUNAS = [
    ("Proposição", 18), ("Casa", 22), ("Data de apresentação", 20),
    ("Tema", 30), ("Setor", 16), ("Autor", 28), ("Coautores", 26),
    ("Situação", 34), ("Comissão", 24), ("Relator", 24),
    ("Ementa", 90), ("Link", 60),
]
SETOR = "Educação"


def _aba(wb: Workbook, nome: str, cabecalho: List[str],
         linhas: List[List[Any]], larguras: List[int]) -> None:
    sh = wb.create_sheet(nome)
    for col, titulo in enumerate(cabecalho, start=1):
        cel = sh.cell(row=1, column=col, value=titulo)
        cel.font = Font(name=FONTE, size=11, bold=True, color="FFFFFFFF")
        cel.fill = PatternFill(fill_type="solid", fgColor="001F4E79")
        cel.alignment = Alignment(horizontal="center", vertical="center",
                                  wrap_text=True)
    for r, linha in enumerate(linhas, start=2):
        for c, valor in enumerate(linha, start=1):
            cel = sh.cell(row=r, column=c, value=valor)
            cel.font = Font(name=FONTE, size=10, color="FF333333")
            cel.border = _BORDA
            cel.alignment = Alignment(vertical="top", wrap_text=True)
            if r % 2 == 0:
                cel.fill = PatternFill(fill_type="solid", fgColor="00F2F7FB")
    for i, largura in enumerate(larguras, start=1):
        sh.column_dimensions[get_column_letter(i)].width = largura
    sh.freeze_panes = "A2"
    if linhas:
        sh.auto_filter.ref = f"A1:{get_column_letter(len(cabecalho))}{len(linhas) + 1}"


def gerar_xlsx(itens: List[Dict[str, Any]], total_bruto: int) -> Path:
    wb = Workbook()
    wb.remove(wb.active)

    # 1) Proposições (aba principal, com todos os campos pedidos)
    linhas = [
        [
            _sigla(i),
            NOMES_CASA.get(i["fonte"], i["fonte"]),
            _data_br(i["data_apresentacao"]),
            i.get("subtema") or "—",
            SETOR,
            _autor(i),
            ", ".join(i.get("coautores") or []) or "—",
            situacao_curta(i.get("situacao")),
            i.get("comissao_atual") or "—",
            _relator(i) or "—",
            _ementa(i),
            i.get("url") or "—",
        ]
        for i in itens
    ]
    _aba(wb, "Proposições", [c for c, _ in COLUNAS], linhas,
         [w for _, w in COLUNAS])

    # 2) Por tema
    contagem = Counter(i.get("subtema") or "Outros temas de educação" for i in itens)
    linhas_tema = [[t, str(n), str(round(100 * n / len(itens), 1)) + "%"]
                   for t, n in sorted(contagem.items(), key=lambda x: (-x[1], x[0]))]
    _aba(wb, "Por tema", ["Tema", "Proposições", "% do total"], linhas_tema,
         [40, 14, 14])

    # 3) Por casa
    por_casa = Counter(i["fonte"] for i in itens)
    linhas_casa = [[NOMES_CASA.get(c, c), str(n),
                    str(round(100 * n / len(itens), 1)) + "%"]
                   for c, n in por_casa.most_common()]
    _aba(wb, "Por casa", ["Casa legislativa", "Proposições", "% do total"],
         linhas_casa, [26, 14, 14])

    # 4) Por autor
    contagem_autor = Counter(_autor(i) for i in itens)
    linhas_autor = [[a, str(n)] for a, n in contagem_autor.most_common()]
    _aba(wb, "Por autor", ["Autoria", "Proposições"], linhas_autor, [34, 14])

    # 5) Metodologia
    linhas_met = [
        ["Período analisado", f"{JANELA_BR} (data de apresentação)"],
        ["Fontes", "Câmara dos Deputados (API de Dados Abertos); Senado Federal "
                   "(API de Dados Abertos)"],
        ["Execução", "Sob demanda (sem agendamento, polling ou tarefas de fundo)"],
        ["Método", "Varredura integral do período por sigla (PL, PLP, PEC, PDC, "
                   "MPV na Câmara; PL, PLP, PEC, PLN, PDL no Senado) com filtro "
                   "local de relevância do setor Educação"],
        ["Termos de interesse", "; ".join(TERMOS_PEDIDO)],
        ["Proposições varidas no período", str(total_bruto)],
        ["Proposições do setor Educação", str(len(itens))],
        ["Data da extração", date.today().strftime("%d/%m/%Y")],
        ["Fonte de dados (JSON)", FONTE_JSON.name],
    ]
    _aba(wb, "Metodologia", ["Item", "Detalhe"], linhas_met, [34, 110])

    ARQ_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ARQ_XLSX)
    return ARQ_XLSX


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if not FONTE_JSON.exists():
        raise SystemExit(f"JSON de coleta não encontrado: {FONTE_JSON}\n"
                         "Rode antes: .venv\\Scripts\\python.exe coletar_educacao.py")
    payload = json.loads(FONTE_JSON.read_text(encoding="utf-8"))
    itens = payload["proposicoes"]
    total_bruto = payload.get("total_bruto", len(itens))

    docx = gerar_docx(itens, total_bruto)
    xlsx = gerar_xlsx(itens, total_bruto)

    print(f"DOCX: {docx}")
    print(f"XLSX: {xlsx}")
    print(f"Proposições do setor: {len(itens)} "
          f"(Câmara {sum(1 for i in itens if i['fonte'] == 'camara')} · "
          f"Senado {sum(1 for i in itens if i['fonte'] == 'senado')})")
    print("Temas: " + ", ".join(
        f"{t}={n}" for t, n in Counter(
            i.get("subtema") or "Outros" for i in itens).most_common()))


if __name__ == "__main__":
    main()