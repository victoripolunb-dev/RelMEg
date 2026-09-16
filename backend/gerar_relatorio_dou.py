# -*- coding: utf-8 -*-
"""Gera o relatório .docx do DOU de 15/09/2026 — Setor de Energia.

Replica a estrutura e a formatação do modelo reproduzido em
"Modelo base/relatorio_dou_energia_2026-09-02.docx".
"""
import json
import os
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

DATA = "2026-09-15"
DATA_DISPLAY = "15/09/2026"

BASE = Path(__file__).parent
FONTE_JSON = BASE / f"dou_energia_{DATA}_com_secao.json"
FONTE_RESUMOS = BASE / f"resumos_dou_{DATA}.json"
# "Conteúdo:" usa resumo analítico quando existir; ementas curtas ficam na íntegra
LIMITE_EMENTA_INTEGRA = 240
LIMITE_EMENTA_TRUNCADA = 300
DIR_ENTREGA = Path(r"C:\Users\Victor\Desktop\RelMeg - Entregas\Relatórios\DOU")
ARQUIVO_SAIDA = Path(os.environ.get("RELMEG_SAIDA",
                     str(DIR_ENTREGA / f"relatorio_dou_energia_{DATA}.docx")))

CZ = RGBColor(0x33, 0x33, 0x33)   # 333333
CINZA = RGBColor(0x66, 0x66, 0x66)  # 666666
AZUL = RGBColor(0x1F, 0x4E, 0x79)  # 1F4E79
VERMELHO = RGBColor(0xFF, 0x00, 0x00)  # FF0000
FONTE = "Montserrat"

# Associações monitoradas
ASSOC = ["ABRADEE", "ABEEólica", "ABiogás", "ABIAPE", "ABRAGE", "ABRATE"]

# Órgãos do universo MME/ANEEL/ANP (escopo do usuário de energia elétrica/gás)
MME_ORGS = ("agência nacional de energia elétrica",
            "agência nacional do petróleo",
            "ministério de minas e energia")

# Tipos considerados normativos (entram em "Ato normativo/despacho ANEEL-MME")
TIPO_NORM = {"despacho", "resolução", "portaria", "comunicado", "autorização"}

# Tipos/sinais de contratação/licitação (categoria compacta, sem detalhamento)
SINAIS_CONTRATO = ("licitação", "extrato", "edital", "retificação", "aviso",
                   "dispensa", "pregão", "homologação", "adjudicação",
                   "convênio", "chamamento", "inexigibilidade")

# Palavras de geração renovável e contexto de geração (categoria detalhada)
RENOV_KEY = ("solar", "fotovol", "eólica", "eolica", "biogás", "biogas",
             "biomassa", "hidrogênio", "hidrogenio", "renovável", "renovavel",
             "renováveis", "renovaveis", "reidi")
RENOV_CTX = ("usina", "geração", "geracao", "geraçã", "fotovol", "reidi",
             "parque", "bess", "central", "subestação", "capacidade instalada")


def texto(item):
    return " ".join([item.get("titulo") or "", item.get("orgao") or "",
                     item.get("ementa") or ""]).lower()


def eh_transmissao(item):
    a = texto(item)
    return any(s in a for s in ("transmissora", "transmissão", "subestaç"))


def eh_fotovoltaica(item):
    a = texto(item)
    return "fotovol" in a or "geração distribuída" in a or "geracao distribuida" in a


def classificar(item):
    """Retorna (categoria, foco). Categorias:
    ato | renov | mercado | infra | leiloes | assoc | contrato | outros
    """
    o = (item.get("orgao") or "").lower()
    t = (item.get("tipo") or "").lower()
    a = texto(item)

    # Mineração (ANM) está fora do escopo energético — não entra no universo MME
    if "agência nacional de mineração" in o:
        if any(s in t for s in SINAIS_CONTRATO):
            return "contrato", ""
        return "outros", ""

    # 1. Universo MME/ANEEL/ANP
    if any(s in o for s in MME_ORGS):
        if t in TIPO_NORM:
            foco = "Ato normativo/despacho ANEEL-MME"
            if eh_transmissao(item):
                foco += "; Infraestrutura (transmissora de energia)"
            elif eh_fotovoltaica(item):
                foco += "; Renovaveis (fotovoltaica)"
            return "ato", foco
        return "contrato", ""

    # 2. Geração renovável
    if any(s in a for s in RENOV_KEY) and any(s in a for s in RENOV_CTX) and \
            ("energi" in a or "reidi" in a):
        if "reidi" in a:
            return "renov", "Renovaveis (reidi)"
        if "bess" in a or "fotovol" in a:
            return "renov", "Renovaveis (fotovoltaica / armazenamento)"
        return "renov", "Renovaveis (renovaveis)"

    # 3. Mercado e tarifas (fiscal/tributário/financeiro do setor)
    if "cotepe" in a:
        return "mercado", "Mercado e tarifas (ICMS sobre combustíveis)"
    if "cmn" in (item.get("titulo") or "").lower() and "energia" in a:
        return "mercado", "Mercado e tarifas (financiamento e garantias)"
    if "alf/sts" in a and "petróleo" in a:
        return "mercado", "Mercado e tarifas (exportação de petróleo)"
    if "materiais elétricos" in a and "substituição tributária" in a:
        return "mercado", "Mercado e tarifas (substituição tributária)"

    # 4. Infraestrutura (usina/transmissão fora do MME)
    if any(s in a for s in ("usina", "linha de transmissão", "subestaç")) and "energi" in a:
        return "infra", "Infraestrutura (usina / transmissão)"

    # 5. Leilões/outorgas
    if "leilão de energia" in a or ("leilão" in a and "energi" in a and "outorga" in a):
        return "leiloes", "Leiloes e outorgas (leilão de energia)"

    # 6. Associações do setor
    for nome in ASSOC:
        if re.search(r"\b" + re.escape(nome), a, re.IGNORECASE) and "energi" in a:
            return "assoc", f"Associacoes do setor ({nome})"

    # 7. Contratações e licitações
    if any(s in t for s in SINAIS_CONTRATO):
        return "contrato", ""

    # 8. Fora do escopo
    return "outros", ""


def normal(doc):
    st = doc.styles["Normal"]
    st.font.name = FONTE
    st.font.size = Pt(10)
    st.font.color.rgb = CZ
    rpr = st.element.get_or_add_rPr()
    rpr.rFonts.set(qn("w:eastAsia"), FONTE)
    pf = st.paragraph_format
    pf.line_spacing = 1.15
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def para(doc, before=None, after=0, align=None):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    if before is not None:
        pf.space_before = before
    pf.space_after = after
    if align is not None:
        pf.alignment = align
    else:
        pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    return p


def run(p, text, size, bold=False, color=CZ, italic=False):
    r = p.add_run(text)
    r.font.name = FONTE
    rpr = r._element.get_or_add_rPr()
    rpr.rFonts.set(qn("w:eastAsia"), FONTE)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    return r


def hyperlink(p, url, text, size=11, bold=True, color=VERMELHO):
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
    rf.set(qn("w:ascii"), FONTE)
    rf.set(qn("w:hAnsi"), FONTE)
    rf.set(qn("w:eastAsia"), FONTE)
    rpr.append(rf)
    if bold:
        rpr.append(rpr.makeelement(qn("w:b"), {}))
    rpr.append(rpr.makeelement(qn("w:sz"), {qn("w:val"): str(size * 2)}))
    rpr.append(rpr.makeelement(qn("w:color"), {qn("w:val"): "FF0000"}))
    r.append(rpr)
    t = r.makeelement(qn("w:t"), {})
    t.text = text
    r.append(t)
    hl.append(r)
    p._p.append(hl)


def secao_header(doc, titulo):
    p = para(doc, before=152400, after=50800)
    run(p, titulo, 12, bold=True, color=AZUL)


def secao_topico(doc, titulo):
    p = para(doc, before=152400, after=50800)
    run(p, titulo, 14, bold=True, color=AZUL)


def celula(cell, text, size=10, bold=False, color=CZ):
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = None
    p.paragraph_format.space_after = None
    if text:
        return run(p, text, size, bold=bold, color=color)


def tabela_resumo(doc, linhas):
    tb = doc.add_table(rows=1, cols=2)
    tb.style = "Table Grid"
    celula(tb.rows[0].cells[0], "Categoria", bold=True)
    celula(tb.rows[0].cells[1], "Publicações", bold=True)
    for nome, n in linhas:
        row = tb.add_row()
        celula(row.cells[0], nome)
        celula(row.cells[1], str(n))
    return tb


def tabela_contratos(doc, itens):
    tb = doc.add_table(rows=1, cols=3)
    tb.style = "Table Grid"
    celula(tb.rows[0].cells[0], "Publicação", bold=True)
    celula(tb.rows[0].cells[1], "Órgão", bold=True)
    celula(tb.rows[0].cells[2], "Detalhes", bold=True)
    for it in itens:
        row = tb.add_row()
        hyperlink(row.cells[0].paragraphs[0], it.get("url") or "",
                  it.get("titulo") or "—", size=9)
        celula(row.cells[1], it.get("orgao") or "", size=9)
        celula(row.cells[2],
               f"{it.get('tipo') or '—'} | p.{it.get('pagina') or '—'}", size=9)
    return tb


def carregar_resumos():
    if not FONTE_RESUMOS.exists():
        return {}
    with open(FONTE_RESUMOS, encoding="utf-8") as f:
        return json.load(f)


def conteudo_item(item, resumos):
    resumo = resumos.get(item.get("url") or "")
    if resumo:
        return resumo
    ementa = item.get("ementa") or ""
    if len(ementa) <= LIMITE_EMENTA_INTEGRA:
        return ementa
    return ementa[:LIMITE_EMENTA_TRUNCADA] + "…"


def item_detalhado(doc, item, foco, resumo):
    p = para(doc, before=101600, after=25400)
    hyperlink(p, item["url"], item["titulo"] or "Publicação")
    p1 = para(doc)
    run(p1, "Órgão: ", 10, bold=True, color=CZ)
    run(p1, item.get("orgao") or "", 10, color=CZ)
    p2 = para(doc)
    run(p2, "Detalhes: ", 10, bold=True, color=CZ)
    run(p2, f"{item.get('tipo') or '—'} | Pág. {item.get('pagina') or '—'} "
           f"| Edição {item.get('edicao') or (item.get('data_publicacao') or '—')}",
        10, color=CINZA)
    p3 = para(doc)
    run(p3, "Foco: ", 10, bold=True, color=CZ)
    run(p3, foco, 10, color=CINZA)
    p4 = para(doc)
    run(p4, "Conteúdo: ", 10, bold=True, color=CZ)
    run(p4, conteudo_item(item, resumo), 10, color=CZ)


def bloco_itens(doc, itens, focos=None, resumos=None):
    resumos = resumos or {}
    for i, it in enumerate(itens):
        if i > 0:
            para(doc)
        item_detalhado(doc, it, (focos or {}).get(id(it), ""), resumos)


def main():
    with open(FONTE_JSON, encoding="utf-8") as f:
        dados = json.load(f)
    resumos = carregar_resumos()

    sec_itens = {s: [] for s in (1, 2, 3)}
    cats_sec = {s: {k: [] for k in ("ato", "renov", "mercado", "infra", "leiloes",
                                     "assoc", "contrato", "outros")} for s in (1, 2, 3)}
    total_sec = {s: 0 for s in (1, 2, 3)}
    focos = {}
    for it in dados:
        sec = int(it.get("secao", 1))
        if sec not in cats_sec:
            sec = 1
        cat, foco = classificar(it)
        cats_sec[sec][cat].append(it)
        focos[id(it)] = foco

    def ordem(item, tipo_pref):
        return (tipo_pref.get(item.get("tipo"), 99), item.get("titulo") or "")

    PREF = {"Resolução": 0, "Despacho": 1, "Portaria": 2, "Autorização": 3,
            "Comunicado": 4}
    for s in (1, 2, 3):
        total_sec[s] = sum(len(v) for v in cats_sec[s].values())
        for key in ("ato", "renov", "mercado", "infra", "leiloes", "assoc"):
            cats_sec[s][key].sort(key=lambda it: ordem(it, PREF))
        cats_sec[s]["contrato"].sort(key=lambda it: ordem(it, {"": 0}))

    n_ato = sum(len(cats_sec[s]["ato"]) for s in (1, 2, 3))
    n_renov = sum(len(cats_sec[s]["renov"]) for s in (1, 2, 3))
    n_mercado = sum(len(cats_sec[s]["mercado"]) for s in (1, 2, 3))
    n_infra = sum(len(cats_sec[s]["infra"]) for s in (1, 2, 3))
    n_leil = sum(len(cats_sec[s]["leiloes"]) for s in (1, 2, 3))
    n_assoc = sum(len(cats_sec[s]["assoc"]) for s in (1, 2, 3))
    n_contrato = sum(len(cats_sec[s]["contrato"]) for s in (1, 2, 3))
    n_outros = sum(len(cats_sec[s]["outros"]) for s in (1, 2, 3))
    total = sum(total_sec.values())
    assert sum(n for n in (n_ato, n_renov, n_mercado, n_infra, n_leil, n_assoc,
                           n_contrato, n_outros)) == total, "contagens inconsistentes"

    doc = Document()
    normal(doc)
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = 1143000
        sec.top_margin = sec.bottom_margin = 914400

    # Cabeçalho
    p = para(doc, before=127000, after=76200, align=WD_ALIGN_PARAGRAPH.CENTER)
    run(p, f"Varredura DOU — {DATA_DISPLAY} — Setor de Energia", 16, bold=True, color=CZ)
    p = para(doc)
    run(p, "Relatório executivo das publicações do Diário Oficial da União relevantes "
           "para os clientes do setor de energia (associações, operadores de "
           "geração/transmissão/distribuição e comercialização).", 10, color=CINZA)

    # Metodologia
    secao_header(doc, "Metodologia")
    p = para(doc)
    run(p, "- Data analisada: **" + DATA + "** — Seções 1, 2 e 3 do DOU.", 9)
    p = para(doc)
    run(p, "- Fonte: portal da Imprensa Nacional (motor de busca SR do in.gov.br), "
           "via API local RelMeg.", 9)
    p = para(doc)
    run(p, "- Termos pesquisados: ABRADEE, ABEEólica, ABiogás, ABIAPE, ABRAGE, "
           "ABRATE, Renova Energia, ANEEL, energia elétrica, concessão de energia, "
           "leilão de energia, distribuidora de energia, energia eólica, energia "
           "solar, biogás, biomassa, hidrogênio, tarifa de energia, mercado livre "
           "de energia, Ministério de Minas e Energia; termos de suporte: gás "
           "natural, petróleo, combustíveis, usina e transmissão elétrica.", 9)
    p = para(doc)
    run(p, "- Execução sob demanda (AGENTS.md), respeitando o rate limit da rota "
           "(10/min).", 9)

    # Achado metodológico
    secao_header(doc, "Achado metodológico importante")
    p = para(doc)
    run(p, 'O portal de busca tokeniza a consulta (não faz busca por frase exata). '
           'Por isso términos genéricos — como "Ministério de Minas e Energia" e '
           '"energia elétrica" — também capturaram publicações de setores '
           'não-energéticos (universidades, prefeituras, bancos e outros '
           'ministérios). A classificação é feita aqui pelo conteúdo '
           '(título + órgão + ementa).', 9)

    # Resumo por categoria
    secao_header(doc, "Resumo por categoria")
    tabela_resumo(doc, [
        ("Associações do setor", n_assoc),
        ("Ato normativo/despacho ANEEL-MME", n_ato),
        ("Leilões e outorgas", n_leil),
        ("Geração renovável", n_renov),
        ("Mercado e tarifas", n_mercado),
        ("Infraestrutura", n_infra),
        ("Contratações e licitações", n_contrato),
    ])
    p = para(doc, before=101600, after=25400)
    run(p, f"Total de publicações únicas localizadas: **{total}**. "
           f"(Seção 1 = {total_sec[1]} · Seção 2 = {total_sec[2]} · "
           f"Seção 3 = {total_sec[3]})", 10)

    # Menções a associações e ANEEL/MME
    secao_header(doc, "Menções às associações e ANEEL/MME")
    p = para(doc)
    run(p, "- **ABRADEE, ABEEólica, ABiogás, ABIAPE, ABRAGE, ABRATE:** nenhuma "
           f"menção direta no DOU de {DATA_DISPLAY} (resultado negativo relevante).", 10)
    p = para(doc)
    run(p, "- **ANEEL / MME:** ver itens das seções detalhadas abaixo (despachos, "
           "resoluções e portarias).", 10)

    # Corpo organizado por Seção do DOU, na ordem 1 -> 2 -> 3
    CATEGORIAS = [
        ("ato", "Ato normativo/despacho ANEEL-MME", "det"),
        ("renov", "Geração renovável", "det"),
        ("mercado", "Mercado e tarifas", "det"),
        ("infra", "Infraestrutura", "det"),
        ("leiloes", "Leilões e outorgas", "det"),
        ("assoc", "Associações do setor", "det"),
        ("contrato", "Contratações e licitações", "tab"),
        ("outros", None, "nota"),
    ]
    NOME_SECAO = ["", "Leis / atos normativos e administrativos",
                  "Pessoal", "Contratos / licitações e editais"]

    for s in (1, 2, 3):
        secao_topico(doc, f"Seção {s} — {NOME_SECAO[s]} ({total_sec[s]})")
        for key, nome, modo in CATEGORIAS:
            itens = cats_sec[s][key]
            if not itens:
                continue
            if modo == "tab":
                p = para(doc, before=101600, after=25400)
                run(p, f"{nome} ({len(itens)})", 12, bold=True, color=AZUL)
                p = para(doc)
                run(p, "Relação compacta (detalhes completos no arquivo JSON de apoio):", 10)
                tabela_contratos(doc, itens)
            elif modo == "nota":
                p = para(doc, before=101600, after=25400)
                run(p, f"Outros ({len(itens)} publicações)", 12, bold=True, color=AZUL)
                p = para(doc)
                run(p, "Menções genéricas ou ocorrências de substring de setores "
                       "não-energéticos (ex.: contratos agro do Mapa, atos de pessoal, "
                       "alvarás de mineração). Conferir no JSON de apoio, se desejado.", 9)
            else:
                secao_header(doc, f"{nome} ({len(itens)})")
                bloco_itens(doc, itens, focos, resumos)

    # Rodapé
    p = para(doc)
    run(p, f"— Gerado automaticamente pela varredura on-demand do DOU (RelMeg). "
           f"Dados completos em dou_energia_{DATA}.json.", 8, color=CINZA)

    DIR_ENTREGA.mkdir(parents=True, exist_ok=True)
    doc.save(ARQUIVO_SAIDA)

    print(f"Salvo: {ARQUIVO_SAIDA}")
    print(f"Total: {total} — Seção 1 = {total_sec[1]} · Seção 2 = {total_sec[2]} "
          f"· Seção 3 = {total_sec[3]}")
    print(f"ATO={n_ato} RENOV={n_renov} MERCADO={n_mercado} INFRA={n_infra} "
          f"LEILOES={n_leil} ASSOC={n_assoc} CONTRATO={n_contrato} OUTROS={n_outros}")
    print("Foco dos itens detalhados:")
    for key, nome in (("ato", "ATO"), ("renov", "RENOV"), ("mercado", "MERCADO")):
        for s in (1, 2, 3):
            for it in cats_sec[s][key]:
                print(f"  S{s} {nome} | {it['titulo'][:55]:55} | {focos[id(it)]}")


if __name__ == "__main__":
    main()