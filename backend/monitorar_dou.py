# -*- coding: utf-8 -*-
"""Varredura GENÉRICA e on-demand do DOU (RelMeg).

Diferente do ``coletar_dou.py`` + ``gerar_relatorio_dou.py`` (fixos no setor de
energia), este motor aceita QUALQUER cliente/tema/interesse:

    python monitorar_dou.py --nome "Cliente X" --interesse "Breve contexto" \\
        --palavras "ANS; ANVISA; planos de saúde; hospitais" --data 2026-09-15

Ou usando um perfil salvo em ``backend/perfis_dou/``:

    python monitorar_dou.py --perfil energia --data 2026-09-15

O perfil também pode ser registrado na pasta ``perfis_dou`` (--salvar-perfil),
seguindo o esquema de ``perfis_dou/energia.json``.

Regras:
  - 100% sob demanda (AGENTS.md): o operador dispara o comando; nada roda em
    segundo plano nem por agendamento.
  - Respeita o rate limit: intervalo >= 6,5s entre requisições ao portal.
  - Entregas gravadas em <dir_entregas>/Relatórios/DOU (RelMeg - Entregas),
    sempre fora do repositório: ``relatorio_dou_<slug>_<data>.docx`` +
    ``dou_<slug>_<data>.json`` (apoio).

Classificação genérica (por item):
  - ato       -> tipo normativo (resolução/despacho/portaria/...) ou órgão do perfil
  - contrato  -> sinais de contratação/licitação
  - mencao    -> qualquer palavra-chave do perfil presente no texto
  - outros    -> ruído de substring (capturado pela busca tokenizada)

Resumos opcionais: se existir ``resumos_dou_<data>_<slug>.json`` com resumos
por URL, eles entram como "Conteúdo:" (fallback: ementa curta ou truncada).
"""
from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from config import settings
from routers.dou import _coletar_portal

# ---------------------------------------------------------------------------
# Parâmetros operacionais (mesmos do fluxo de energia)
# ---------------------------------------------------------------------------
INTERVALO = 6.5
LIMITE_POR_TERMO = 200
SECOES_PADRAO = (1, 2, 3)
DIR_PERFIS = Path(__file__).parent / "perfis_dou"
DIR_ENTREGA = settings.dir_relatorios / "DOU"

# ---------------------------------------------------------------------------
# Identidade visual (idêntica ao relatório de energia aprovado)
# ---------------------------------------------------------------------------
CZ = RGBColor(0x33, 0x33, 0x33)      # 333333
CINZA = RGBColor(0x66, 0x66, 0x66)   # 666666
AZUL = RGBColor(0x1F, 0x4E, 0x79)    # 1F4E79
VERMELHO = RGBColor(0xFF, 0x00, 0x00)  # FF0000
FONTE = "Montserrat"
LIMITE_EMENTA_INTEGRA = 240
LIMITE_EMENTA_TRUNCADA = 300

TIPO_NORM = {"despacho", "resolução", "portaria", "comunicado", "autorização",
             "instrução normativa", "decreto", "lei"}
SINAIS_CONTRATO = ("licitação", "extrato", "edital", "retificação", "aviso",
                   "dispensa", "pregão", "homologação", "adjudicação",
                   "convênio", "chamamento", "inexigibilidade")

NOME_SECAO = ["", "Leis / atos normativos e administrativos",
              "Pessoal", "Contratos / licitações e editais"]

CATEGORIAS = [
    ("ato", "Ato normativo/despacho", "det"),
    ("mencao", "Menções diretas ao tema", "det"),
    ("contrato", "Contratações e licitações", "tab"),
    ("outros", None, "nota"),
]


# ---------------------------------------------------------------------------
# Perfis
# ---------------------------------------------------------------------------
def slugify(nome: str) -> str:
    nome = unicodedata.normalize("NFD", nome).encode("ascii", "ignore").decode()
    nome = re.sub(r"[^A-Za-z0-9]+", "_", nome).strip("_").lower()
    return nome or "perfil"


def _split_lista(valor) -> list[str]:
    if isinstance(valor, list):
        return [str(v).strip() for v in valor if str(v).strip()]
    itens = re.split(r"[;\n]+", str(valor or ""))
    return [i.strip() for i in itens if i.strip()]


def carregar_perfil(nome: str) -> dict:
    arquivo = Path(nome)
    if not arquivo.is_absolute():
        candidato = DIR_PERFIS / (nome if nome.endswith(".json") else nome + ".json")
        if candidato.exists():
            arquivo = candidato
    with open(arquivo, encoding="utf-8") as f:
        perfil = json.load(f)
    perfil["nome"] = perfil.get("nome") or Path(arquivo).stem
    perfil["interesse"] = perfil.get("interesse") or ""
    perfil["palavras_chave"] = _split_lista(perfil.get("palavras_chave"))
    perfil["orgaos"] = _split_lista(perfil.get("orgaos"))
    return perfil


def salvar_perfil(nome: str, perfil: dict) -> Path:
    DIR_PERFIS.mkdir(parents=True, exist_ok=True)
    destino = DIR_PERFIS / f"{slugify(nome)}.json"
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(perfil, f, ensure_ascii=False, indent=2)
    return destino


# ---------------------------------------------------------------------------
# Busca (on-demand, com rate limit)
# ---------------------------------------------------------------------------
def coletar(perfil: dict, data: str, secoes) -> list[dict]:
    por_secao: dict[int, list[dict]] = {s: [] for s in secoes}
    for secao in secoes:
        by_url: dict[str, dict] = {}
        print(f"--- SEÇÃO {secao} ({NOME_SECAO[secao]}) ---")
        for q in perfil["palavras_chave"]:
            inicio = time.time()
            resultados = _coletar_portal(q, secao, data, limite=LIMITE_POR_TERMO)
            novos = 0
            for r in resultados:
                u = r.get("url") or ""
                if u and u not in by_url:
                    r["secao"] = secao
                    by_url[u] = r
                    por_secao[secao].append(r)
                    novos += 1
            print(f"[S{secao}] [{q}] {len(resultados)} resultados | {novos} novos "
                  f"| seção {len(por_secao[secao])}")
            decorrido = time.time() - inicio
            if decorrido < INTERVALO:
                time.sleep(INTERVALO - decorrido)
    return por_secao[secoes[0]] + [it for s in secoes[1:] for it in por_secao[s]]


# ---------------------------------------------------------------------------
# Classificação genérica
# ---------------------------------------------------------------------------
def texto(item) -> str:
    return " ".join([item.get("titulo") or "", item.get("orgao") or "",
                     item.get("ementa") or ""]).lower()


def classificar(item, perfil):
    """Retorna (categoria, foco). Categorias: ato | mencao | contrato | outros.

    Relevância (atório normativo/contrato) exige: OU órgão do universo
    do perfil, OU palavra-chave do perfil presente no texto. Caso
    contrário o item é classificado como "outros" (ruído da tokenização
    da busca no portal — substring que não bate exatamente).
    """
    o = (item.get("orgao") or "").lower()
    t = (item.get("tipo") or "").lower()
    a = texto(item)
    orgaos = [x.lower() for x in perfil.get("orgaos") or []]
    palavras = [x.lower() for x in perfil.get("palavras_chave") or []]
    hits = [p for p in palavras if p in a]

    # 1. Órgão do universo do perfil -> ato (normativo) ou contrato
    if orgaos and any(s in o for s in orgaos):
        if t in TIPO_NORM:
            return "ato", "Ato normativo/despacho"
        return "contrato", ""

    # 2. Tipo normativo genérico — somente se palavra-chave presente
    if t in TIPO_NORM and hits:
        return "ato", "Ato normativo/despacho"

    # 3. Contratações/licitações — somente se palavra-chave presente
    if any(s in t for s in SINAIS_CONTRATO) and hits:
        return "contrato", ""

    # 4. Menção direta (qualquer palavra-chave no texto)
    if hits:
        foco = "; ".join(hits[:3])
        return "mencao", f"Palavras-chave: {foco}"

    # 5. Fora do escopo (ruído)
    return "outros", ""


# ---------------------------------------------------------------------------
# Formatação do .docx (mesma identidade do relatório de energia)
# ---------------------------------------------------------------------------
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
    pf.alignment = align if align is not None else WD_ALIGN_PARAGRAPH.JUSTIFY
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


def carregar_resumos(caminho):
    if caminho is None or not Path(caminho).exists():
        return {}
    with open(caminho, encoding="utf-8") as f:
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


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------
def gerar_relatorio(perfil, dados, data, data_display, resumos, secoes):
    sec_itens = {s: [] for s in SECOES_PADRAO}
    cats_sec = {s: {k: [] for k in ("ato", "mencao", "contrato", "outros")}
                for s in SECOES_PADRAO}
    total_sec = {s: 0 for s in SECOES_PADRAO}
    focos = {}
    for it in dados:
        sec = int(it.get("secao", 1))
        if sec not in cats_sec:
            sec = 1
        cat, foco = classificar(it, perfil)
        cats_sec[sec][cat].append(it)
        focos[id(it)] = foco

    PREF = {"Resolução": 0, "Despacho": 1, "Portaria": 2, "Autorização": 3,
            "Comunicado": 4}
    for s in SECOES_PADRAO:
        total_sec[s] = sum(len(v) for v in cats_sec[s].values())
        for key in ("ato", "mencao"):
            cats_sec[s][key].sort(key=lambda it: (
                PREF.get(it.get("tipo"), 99), it.get("titulo") or ""))
        cats_sec[s]["contrato"].sort(key=lambda it: it.get("titulo") or "")

    n_ato = sum(len(cats_sec[s]["ato"]) for s in SECOES_PADRAO)
    n_mencao = sum(len(cats_sec[s]["mencao"]) for s in SECOES_PADRAO)
    n_contrato = sum(len(cats_sec[s]["contrato"]) for s in SECOES_PADRAO)
    n_outros = sum(len(cats_sec[s]["outros"]) for s in SECOES_PADRAO)
    total = sum(total_sec.values())
    assert (n_ato + n_mencao + n_contrato + n_outros) == total, "contagens inconsistentes"

    doc = Document()
    normal(doc)
    for sec in doc.sections:
        sec.left_margin = sec.right_margin = 1143000
        sec.top_margin = sec.bottom_margin = 914400

    # Cabeçalho
    p = para(doc, before=127000, after=76200, align=WD_ALIGN_PARAGRAPH.CENTER)
    run(p, f"Varredura DOU — {data_display} — {perfil['nome']}", 16, bold=True, color=CZ)
    p = para(doc)
    run(p, perfil.get("interesse") or "", 10, color=CINZA)

    # Metodologia
    secoes_texto = "+".join(str(s) for s in secoes)
    secao_header(doc, "Metodologia")
    p = para(doc)
    run(p, f"- Data analisada: **{data}** — Seções {secoes_texto} do DOU.", 9)
    p = para(doc)
    run(p, "- Fonte: portal da Imprensa Nacional (motor de busca SR do in.gov.br), "
           "via API local RelMeg.", 9)
    p = para(doc)
    run(p, "- Termos pesquisados: " + "; ".join(perfil["palavras_chave"]) + ".", 9)
    p = para(doc)
    run(p, "- Execução sob demanda (AGENTS.md), respeitando o rate limit da rota "
           "(10/min).", 9)

    # Achado metodológico
    secao_header(doc, "Achado metodológico importante")
    p = para(doc)
    run(p, 'O portal de busca tokeniza a consulta (não faz busca por frase exata). '
           'Por isso termos genéricos também capturam publicações de setores '
           'não relacionados (universidades, prefeituras, bancos e outros '
           'ministérios). A classificação é feita aqui pelo conteúdo '
           '(título + órgão + ementa).', 9)

    # Resumo por categoria
    secao_header(doc, "Resumo por categoria")
    tabela_resumo(doc, [
        ("Ato normativo/despacho", n_ato),
        ("Menções diretas ao tema", n_mencao),
        ("Contratações e licitações", n_contrato),
    ])
    p = para(doc, before=101600, after=25400)
    run(p, f"Total de publicações únicas localizadas: **{total}**. "
           f"(Seção 1 = {total_sec[1]} · Seção 2 = {total_sec[2]} · "
           f"Seção 3 = {total_sec[3]})", 10)

    # Corpo organizado por Seção do DOU, na ordem 1 -> 2 -> 3
    for s in secoes:
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
                run(p, "Menções genéricas ou ocorrências de substring não relacionadas "
                       "ao tema. Conferir no JSON de apoio, se desejado.", 9)
            else:
                secao_header(doc, f"{nome} ({len(itens)})")
                bloco_itens(doc, itens, focos, resumos)

    # Rodapé
    p = para(doc)
    run(p, f"— Gerado automaticamente pela varredura on-demand do DOU (RelMeg). "
           f"Dados completos em dou_{slugify(perfil['nome'])}_{data}.json.",
        8, color=CINZA)

    DIR_ENTREGA.mkdir(parents=True, exist_ok=True)
    docx_final = DIR_ENTREGA / f"relatorio_dou_{slugify(perfil['nome'])}_{data}.docx"
    doc.save(docx_final)

    print(f"Salvo: {docx_final}")
    print(f"Total: {total} — Seção 1 = {total_sec[1]} · Seção 2 = {total_sec[2]} "
          f"· Seção 3 = {total_sec[3]}")
    print(f"ATO={n_ato} MENCAO={n_mencao} CONTRATO={n_contrato} OUTROS={n_outros}")
    return docx_final


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Varredura genérica on-demand do DOU (RelMeg)")
    ap.add_argument("--perfil", help="Nome de perfil salvo (backend/perfis_dou) ou caminho de JSON")
    ap.add_argument("--nome", help="Nome do cliente/tema (usado com --palavras; cria perfil avulso)")
    ap.add_argument("--interesse", default="", help="Breve contexto/interesse (subtítulo do relatório)")
    ap.add_argument("--palavras", help="Palavras-chave separadas por ; ou enter")
    ap.add_argument("--orgaos", default="", help="Órgãos do universo (opcional, separados por ;)")
    ap.add_argument("--data", required=True, help="Data da edição do DOU (AAAA-MM-DD)")
    ap.add_argument("--secoes", default="1,2,3", help="Seções a varrer (padrão: 1,2,3)")
    ap.add_argument("--salvar-perfil", action="store_true",
                    help="Registra o perfil avulso em backend/perfis_dou para reuso")
    ap.add_argument("--resumos", default=None,
                    help="JSON opcional com resumos por URL (Conteúdo:)")
    ap.add_argument("--reutilizar", default=None,
                    help="Reusa um JSON já coletado (pula a varredura no portal)")
    args = ap.parse_args()

    if args.perfil:
        perfil = carregar_perfil(args.perfil)
    elif args.nome and args.palavras:
        perfil = {
            "nome": args.nome,
            "interesse": args.interesse,
            "palavras_chave": _split_lista(args.palavras),
            "orgaos": _split_lista(args.orgaos),
        }
        if args.salvar_perfil:
            destino = salvar_perfil(args.nome, perfil)
            print(f"Perfil salvo: {destino}")
    else:
        ap.error("Informe --perfil OU --nome+--palavras")

    if not perfil.get("palavras_chave"):
        ap.error("O perfil precisa de ao menos uma palavra-chave.")

    secoes = tuple(int(s) for s in args.secoes.split(",") if s.strip())
    secoes = tuple(s for s in secoes if s in (1, 2, 3))
    if not secoes:
        secoes = SECOES_PADRAO

    data = args.data
    ano, mes, dia = data.split("-")
    data_display = f"{dia}/{mes}/{ano}"

    if args.reutilizar:
        with open(args.reutilizar, encoding="utf-8") as f:
            dados = json.load(f)
        print(f"Reutilizando JSON: {args.reutilizar} ({len(dados)} itens)")
    else:
        dados = coletar(perfil, data, secoes)

    # JSON de apoio <entregas>/Relatórios/DOU
    DIR_ENTREGA.mkdir(parents=True, exist_ok=True)
    apoio = DIR_ENTREGA / f"dou_{slugify(perfil['nome'])}_{data}.json"
    apoio.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Salvo: {apoio} ({len(dados)} itens únicos)")

    resumos = carregar_resumos(args.resumos)
    gerar_relatorio(perfil, dados, data, data_display, resumos, secoes)


if __name__ == "__main__":
    main()