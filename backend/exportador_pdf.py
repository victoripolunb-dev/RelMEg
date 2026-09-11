"""
Relatório Executivo em PDF (reportlab) — exportação corporativa sob demanda.

Estrutura do documento:
    1. Cabeçalho Institucional (RelMeg + cliente/instituição + data de emissão);
    2. Metadados da extração (palavras-chave, período, vínculo com execução);
    3. Bloco de Indicadores (KPIs): totais, tiers, itens aprovados vs. descartados;
    4. Resumo Temático (Family Talks): temas detectados, contagem e eixos;
    5. Tabelas de detalhamento com formatação limpa (azul marinho/cinza),
       paginação automática e rodapé com contagem de páginas.

Caminho de saída: settings.dir_relatorios (<entregas>/Relatórios) via
POST /api/exportar/pdf-executivo (returns JSON ou download com ?baixar=true).

Dependência: reportlab (ver requirements.txt).
"""
from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import database
from config import settings

# ---------------------------------------------------------------------------
# Paleta corporativa (azul marinho/cinza) e geometria
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#1F2A44")
AZUL = colors.HexColor("#2E4A7D")
BRANCO = colors.white
CINZA = colors.HexColor("#5B6472")
CINZA_CLARO = colors.HexColor("#F2F4F7")
LINHA = colors.HexColor("#D8DDE4")
TEXTO = colors.HexColor("#222930")
VERDE = colors.HexColor("#1F7A4D")
AMARELO = colors.HexColor("#8A6D1A")
VERMELHO = colors.HexColor("#9B1C1C")

MARGEM = 14 * mm
LARGURA = A4[0] - 2 * MARGEM

_COLUNAS_PRIORITARIAS = [
    "titulo", "ementa", "autor", "data", "numero", "status", "descricao_situacao",
]


def _estilos() -> Dict[str, ParagraphStyle]:
    """Estilos de parágrafo do relatório."""
    return {
        "titulo": ParagraphStyle(
            "titulo", fontName="Helvetica-Bold", fontSize=20, leading=24,
            textColor=NAVY, alignment=TA_LEFT,
        ),
        "brand": ParagraphStyle(
            "brand", fontName="Helvetica-Bold", fontSize=26, leading=30,
            textColor=NAVY, alignment=TA_LEFT,
        ),
        "subtitulo": ParagraphStyle(
            "subtitulo", fontName="Helvetica", fontSize=9.5, leading=13,
            textColor=CINZA,
        ),
        "sec": ParagraphStyle(
            "sec", fontName="Helvetica-Bold", fontSize=12, leading=15,
            textColor=NAVY, spaceBefore=10, spaceAfter=6,
        ),
        "corpo": ParagraphStyle(
            "corpo", fontName="Helvetica", fontSize=9, leading=12.5,
            textColor=TEXTO,
        ),
        "rotulo_dado": ParagraphStyle(
            "rotulo_dado", fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=CINZA,
        ),
        "valor_dado": ParagraphStyle(
            "valor_dado", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=NAVY,
        ),
        "kpi_valor": ParagraphStyle(
            "kpi_valor", fontName="Helvetica-Bold", fontSize=16, leading=19,
            textColor=NAVY, alignment=TA_CENTER,
        ),
        "kpi_rotulo": ParagraphStyle(
            "kpi_rotulo", fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=CINZA, alignment=TA_CENTER,
        ),
        "kpi_nota": ParagraphStyle(
            "kpi_nota", fontName="Helvetica-Oblique", fontSize=7.5, leading=10,
            textColor=CINZA, alignment=TA_CENTER,
        ),
        "tabela_cab": ParagraphStyle(
            "tabela_cab", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=BRANCO,
        ),
        "tabela_cel": ParagraphStyle(
            "tabela_cel", fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=TEXTO,
        ),
    }


def _esc(texto: Any) -> str:
    """Escape mínimo para Paragraph do reportlab (evita quebra XML)."""
    if texto is None:
        return ""
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _sec(story: List, texto: str, estilos: Dict[str, ParagraphStyle]) -> None:
    story.append(Paragraph(_esc(texto), estilos["sec"]))
    story.append(HRFlowable(color=AZUL, thickness=0.7, width="100%", spaceBefore=2, spaceAfter=8))


def _data_hoje() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def _slug(texto: Any) -> str:
    texto = re.sub(r"[^A-Za-z0-9]+", "-", str(texto or "").strip().lower()).strip("-")
    return texto or "geral"


def _nome_pdf(relatorio: Dict[str, Any]) -> str:
    data = str(relatorio.get("periodo") or datetime.now().date().isoformat()).replace("/", "-")
    return f"Relatorio_Executivo_{_slug(relatorio.get('cliente'))}_{_slug(data)}.pdf"


# ---------------------------------------------------------------------------
# Construção do relatório
# ---------------------------------------------------------------------------

def montar_relatorio_de_clipping(
    resultado: Dict[str, Any],
    titulo: str = "Relatório Executivo",
    cliente: Optional[str] = None,
) -> Dict[str, Any]:
    """Monta o payload do relatório a partir do resultado do Clipping semanal.

    Os totais de Câmara/Senado, temas detectados e descartados vêm do filtro
    institucional Family Talks já aplicado na geração do Clipping.
    """
    filtro = resultado["filtro"] or {}
    contagem = filtro.get("temas_detectados") or {}
    temas = [
        {"tema": nome, "total": int(qtd)}
        for nome, qtd in sorted(contagem.items(), key=lambda x: (-x[1], x[0]))
    ]
    camara = resultado.get("camara") or []
    senado = resultado.get("senado") or []
    total = resultado.get("total") or (len(camara) + len(senado))
    descartados = filtro.get("descartados_total") or 0

    kpis = [
        {
            "rotulo": "Proposições relevantes",
            "valor": str(total),
            "nota": f"{len(camara)} Câmara · {len(senado)} Senado",
        },
        {
            "rotulo": "Temas detectados",
            "valor": str(len(temas)),
            "nota": filtro.get("matriz", "Family Talks"),
        },
        {
            "rotulo": "Aprovados pelo filtro",
            "valor": str(total),
            "nota": "Cruzamento com os temas prioritários",
        },
        {
            "rotulo": "Descartados (filtro institucional)",
            "valor": str(descartados),
            "nota": "Fora do escopo Family Talks",
        },
    ]
    return {
        "titulo": titulo,
        "cliente": cliente or "Family Talks",
        "periodo": resultado.get("periodo"),
        "metadados": {
            "palavras_chave": ", ".join(resultado.get("palavras_chave") or []),
            "ano": str(resultado.get("ano") or ""),
            "matriz": filtro.get("matriz", "Family Talks"),
            "modelo": resultado.get("modelo") or "",
        },
        "kpis": kpis,
        "temas_family_talks": temas,
        "eixos_prioritarios": [t["tema"] for t in temas[:3]],
        "itens": sorted(
            camara + senado,
            key=lambda x: str(x.get("data") or x.get("apresentacao") or ""),
            reverse=True,
        ),
    }


# ---------------------------------------------------------------------------
# Renderização do PDF
# ---------------------------------------------------------------------------

def _rodape(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(CINZA)
    canvas.drawRightString(A4[0] - MARGEM, 10 * mm, f"Página {doc.page} · {_data_hoje()}")
    canvas.drawString(MARGEM, 10 * mm, "RelMeg — Relatório Executivo")
    canvas.restoreState()


def _capa(story: List, relatorio: Dict[str, Any], estilos: Dict[str, ParagraphStyle]) -> None:
    """Cabeçalho institucional: marca RelMeg + cliente + data de emissão."""
    cab = Table(
        [[
            Paragraph("RelMeg", estilos["brand"]),
            Paragraph(
                f"<b>Cliente:</b> {_esc(relatorio.get('cliente') or '—')}<br/>"
                f"<b>Emissão:</b> {_esc(_data_hoje())}",
                estilos["valor_dado"],
            ),
        ]],
        colWidths=[LARGURA * 0.6, LARGURA * 0.4],
    )
    cab.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(cab)
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(color=NAVY, thickness=1.4, width="100%", spaceAfter=6 * mm))
    story.append(Paragraph(_esc(relatorio.get("titulo") or "Relatório Executivo"), estilos["titulo"]))
    story.append(Spacer(1, 2 * mm))


def _metadados(story: List, relatorio: Dict[str, Any], estilos: Dict[str, ParagraphStyle]) -> None:
    """Metadados da extração (chave/valor)."""
    _sec(story, "Metadados da Extração", estilos)
    metadados = dict(relatorio.get("metadados") or {})
    linhas = []
    for chave, valor in metadados.items():
        if valor not in (None, ""):
            linhas.append([" " + str(chave).replace("_", " ").capitalize(), _esc(valor)])
    if not linhas:
        story.append(Paragraph("Sem metadados registrados.", estilos["corpo"]))
        return
    tabela = Table(linhas, colWidths=[LARGURA * 0.32, LARGURA * 0.68])
    tabela.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), CINZA),
        ("TEXTCOLOR", (1, 0), (1, -1), NAVY),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [BRANCO, CINZA_CLARO]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINHA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tabela)
    story.append(Spacer(1, 2 * mm))


def _kpis(story: List, relatorio: Dict[str, Any], estilos: Dict[str, ParagraphStyle]) -> None:
    """Bloco de Indicadores (KPIs) em cards."""
    _sec(story, "Indicadores", estilos)
    kpis = relatorio.get("kpis") or []
    if not kpis:
        story.append(Paragraph("Nenhum indicador registrado.", estilos["corpo"]))
        return

    def _card(kpi: Dict[str, Any]) -> List:
        corpo = [Paragraph(_esc(kpi.get("valor") or "—"), estilos["kpi_valor"])]
        if kpi.get("rotulo"):
            corpo.append(Spacer(1, 1 * mm))
            corpo.append(Paragraph(_esc(kpi["rotulo"]), estilos["kpi_rotulo"]))
        if kpi.get("nota"):
            corpo.append(Spacer(1, 0.5 * mm))
            corpo.append(Paragraph(_esc(kpi["nota"]), estilos["kpi_nota"]))
        celula = Table([[corpo]], colWidths=[LARGURA / 3])
        celula.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CINZA_CLARO),
            ("BOX", (0, 0), (-1, -1), 0.5, LINHA),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return [celula]

    linhas_kpi = [kpis[i:i + 3] for i in range(0, len(kpis), 3)]
    grade = Table(
        [[_card(k) for k in linha] for linha in linhas_kpi],
        colWidths=[LARGURA / 3] * 3,
    )
    grade.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(grade)
    story.append(Spacer(1, 2 * mm))


def _temas(story: List, relatorio: Dict[str, Any], estilos: Dict[str, ParagraphStyle]) -> None:
    """Resumo temático Family Talks: tema, quantidade e %."""
    _sec(story, "Resumo Temático — Family Talks", estilos)
    temas = relatorio.get("temas_family_talks") or []
    if not temas:
        story.append(Paragraph("Nenhum tema detectado nesta extração.", estilos["corpo"]))
        return
    total = sum(int(t.get("total") or 0) for t in temas)
    linhas = [[Paragraph("Tema", estilos["tabela_cab"]), Paragraph("Qtde", estilos["tabela_cab"]),
               Paragraph("%", estilos["tabela_cab"])]]
    for t in temas:
        qtd = int(t.get("total") or 0)
        pct = (qtd / total * 100) if total else 0
        linhas.append([
            Paragraph(_esc(t.get("tema") or "—"), estilos["tabela_cel"]),
            Paragraph(str(qtd), estilos["tabela_cel"]),
            Paragraph(f"{pct:.1f}%", estilos["tabela_cel"]),
        ])
    if total:
        linhas.append([
            Paragraph("<b>Total</b>", estilos["tabela_cel"]),
            Paragraph(f"<b>{total}</b>", estilos["tabela_cel"]),
            Paragraph("<b>100.0%</b>", estilos["tabela_cel"]),
        ])
    tabela = Table(linhas, colWidths=[LARGURA * 0.72, LARGURA * 0.14, LARGURA * 0.14])
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BRANCO, CINZA_CLARO]),
        ("GRID", (0, 0), (-1, -1), 0.4, LINHA),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tabela)
    story.append(Spacer(1, 2 * mm))


def _eixos(story: List, relatorio: Dict[str, Any], estilos: Dict[str, ParagraphStyle]) -> None:
    """Eixos prioritários (top temas)."""
    eixos = relatorio.get("eixos_prioritarios") or []
    if not eixos:
        return
    _sec(story, "Eixos Prioritários", estilos)
    for i, eixo in enumerate(eixos, start=1):
        story.append(Paragraph(
            f"<b>{i}.</b> {_esc(eixo)}",
            ParagraphStyle(
                "eixo", fontName="Helvetica", fontSize=9.5, leading=13.5,
                textColor=TEXTO, leftIndent=6, spaceAfter=2,
            ),
        ))
    story.append(Spacer(1, 2 * mm))


def _itens(story: List, relatorio: Dict[str, Any], estilos: Dict[str, ParagraphStyle]) -> None:
    """Tabela de detalhamento dos itens coletados."""
    itens = relatorio.get("itens") or []
    if not itens:
        return
    _sec(story, "Detalhamento", estilos)
    colunas = [c for c in _COLUNAS_PRIORITARIAS if any(c in (i or {}) for i in itens)]
    if not colunas:
        colunas = [c for c in (itens[0] or {})][:6]
    if not colunas:
        return

    linhas = [[Paragraph(c.replace("_", " ").capitalize(), estilos["tabela_cab"]) for c in colunas]]
    for item in itens:
        linhas.append([Paragraph(_esc(item.get(c)), estilos["tabela_cel"]) for c in colunas])

    larg = [LARGURA / len(colunas)] * len(colunas)
    tabela = Table(linhas, colWidths=larg)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BRANCO, CINZA_CLARO]),
        ("GRID", (0, 0), (-1, -1), 0.4, LINHA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        # Quebra de linha automática em células compridas (ementas).
        ("LEFTPADDING", (0, 1), (-1, -1), 3),
        ("RIGHTPADDING", (0, 1), (-1, -1), 3),
    ]))
    story.append(tabela)


def _story(relatorio: Dict[str, Any]) -> List[Any]:
    estilos = _estilos()
    story: List[Any] = []
    _capa(story, relatorio, estilos)
    _metadados(story, relatorio, estilos)
    _kpis(story, relatorio, estilos)
    _temas(story, relatorio, estilos)
    _eixos(story, relatorio, estilos)
    _itens(story, relatorio, estilos)
    return story


def gerar_pdf_executivo(
    relatorio: Dict[str, Any],
    destino: Optional[Path] = None,
) -> Path:
    """Gera o PDF executivo em disco e retorna o caminho gravado."""
    destino = destino or settings.dir_relatorios / _nome_pdf(relatorio)
    destino.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(destino),
        pagesize=A4,
        leftMargin=MARGEM, rightMargin=MARGEM,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"{relatorio.get('titulo') or 'Relatório Executivo'} — RelMeg",
        author="RelMeg",
    )
    doc.build(_story(relatorio), onFirstPage=_rodape, onLaterPages=_rodape)
    return destino


def bytes_pdf_executivo(relatorio: Dict[str, Any]) -> bytes:
    """Gera o PDF em memória (útil para download sem gravação)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGEM, rightMargin=MARGEM,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"{relatorio.get('titulo') or 'Relatório Executivo'} — RelMeg",
        author="RelMeg",
    )
    doc.build(_story(relatorio), onFirstPage=_rodape, onLaterPages=_rodape)
    conteudo = buffer.getvalue()
    buffer.close()
    return conteudo


async def gerar_pdf_executivo_async(
    relatorio: Dict[str, Any],
    destino: Optional[Path] = None,
    registrar: bool = True,
) -> Dict[str, Any]:
    """Wrapper assíncrono: gera o PDF e registra o evento de auditoria."""
    caminho = await asyncio.to_thread(gerar_pdf_executivo, relatorio, destino)
    if registrar:
        await asyncio.to_thread(
            database.registrar_evento,
            "relatorio_pdf",
            f"PDF executivo gerado: {caminho.name}",
        )
    return {"arquivo": caminho.name, "caminho": str(caminho)}


# ---------------------------------------------------------------------------
# Rota FastAPI
# ---------------------------------------------------------------------------

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.requests import Request

from exportador_local import ClippingError, _gerar_clipping_async
from rate_limit import limiter

router = APIRouter(prefix="/api/exportar", tags=["Exportação — Relatório Executivo PDF"])


class KpiRelatorio(BaseModel):
    rotulo: str
    valor: str
    nota: Optional[str] = None


class TemaRelatorio(BaseModel):
    tema: str
    total: int = 0


class RelatorioExecutivoPayload(BaseModel):
    titulo: str = "Relatório Executivo"
    cliente: Optional[str] = None
    periodo: Optional[str] = None
    numero_execucao: Optional[str] = None
    metadados: Optional[Dict[str, Any]] = None
    kpis: Optional[List[KpiRelatorio]] = None
    temas_family_talks: Optional[List[TemaRelatorio]] = None
    eixos_prioritarios: Optional[List[str]] = None
    itens: Optional[List[Dict[str, Any]]] = None
    # Conveniência: gera a partir do Clipping semanal (sob demanda — AGENTS.md).
    keywords: Optional[str] = None
    sem_filtro: bool = False
    baixar: bool = False


@router.post("/pdf-executivo")
@limiter.limit("5/minute")
async def exportar_pdf_executivo(
    request: Request,
    payload: RelatorioExecutivoPayload,
):
    """Gera o Relatório Executivo em PDF.

    - ``keywords`` preenchido: o relatório é montado automaticamente a partir
      do Clipping semanal (busca Câmara/Senado + Filtro Family Talks).
    - ``keywords`` vazio: usa os KPIs/temas/itens informados no corpo (sem
      dependência do modelo DOCX, ideal para integrações de auditoria).
    - ``baixar=true``: retorna o arquivo; senão, retorna JSON com o caminho.
    """
    palavras = None
    if payload.keywords and payload.keywords.strip():
        palavras = [k.strip() for k in payload.keywords.split(",") if k.strip()]

    if palavras:
        try:
            resultado = await _gerar_clipping_async(
                keywords=palavras,
                periodo=payload.periodo,
                aplicar_filtro_family_talks=not payload.sem_filtro,
            )
        except ClippingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        relatorio = montar_relatorio_de_clipping(
            resultado,
            titulo=payload.titulo,
            cliente=payload.cliente,
        )
        if payload.numero_execucao:
            relatorio["numero_execucao"] = payload.numero_execucao
    else:
        relatorio = {
            "titulo": payload.titulo,
            "cliente": payload.cliente,
            "periodo": payload.periodo,
            "numero_execucao": payload.numero_execucao,
            "metadados": payload.metadados or {},
            "kpis": [k.model_dump() for k in (payload.kpis or [])],
            "temas_family_talks": [t.model_dump() for t in (payload.temas_family_talks or [])],
            "eixos_prioritarios": payload.eixos_prioritarios or [],
            "itens": payload.itens or [],
        }

    salvo = await gerar_pdf_executivo_async(relatorio)

    if payload.baixar:
        from fastapi.responses import Response

        conteudo = await asyncio.to_thread(bytes_pdf_executivo, relatorio)
        return Response(
            content=conteudo,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{salvo["arquivo"]}"',
            },
        )

    return {
        "arquivo": salvo["arquivo"],
        "cliente": relatorio.get("cliente"),
        "periodo": relatorio.get("periodo"),
        "kpis": relatorio.get("kpis"),
        "temas_family_talks": relatorio.get("temas_family_talks"),
        "total_itens": len(relatorio.get("itens") or []),
    }