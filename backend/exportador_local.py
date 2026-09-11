"""
Gerador de Clipping Semanal — "Novas Proposições" baseado em Template Fiel.

Objetivo: automatizar o preenchimento semanal do documento que replica 100% a
identidade visual (margens, fontes, cores, espaçamentos e estrutura) do arquivo
"MODELO A SER SEGUIDO.docx" criado pelo usuário — sem jamais sobrescrever o modelo.

Arquitetura:
    1. Localiza o modelo em "RelMeg - Entregas/Novas proposições/MODELO A SER SEGUIDO.docx".
    2. Busca proposições nas APIs da Câmara e do Senado por palavras-chave.
    3. Abre o modelo com python-docx e PRESERVA sua estrutura: os cabeçalhos de
       seção ("Câmara dos Deputados" / "Senado Federal") e o protótipo de bloco
       (número, ementa, autor, data) são clonados, nunca recriados do zero, para
       manter a formatação original.
    4. Injeta os dados em blocos, com hiperlink funcional no número da proposição.
    5. Salva como "Clipping_Novas_Proposicoes_<periodo>.docx" na MESMA pasta,
       sem tocar no modelo.

Conformidade (AGENTS.md): execução estritamente sob demanda — acionada apenas por
rota HTTP/CLI, nunca por rotina em segundo plano ou cron.
"""
from __future__ import annotations

import asyncio
import copy
import datetime as _dt
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import RGBColor, Pt

from routers.proposicoes import _listar_proposicoes
from routers.senado.materias import _listar_materias_senado
from config import settings
import database
import family_talks

# ---------------------------------------------------------------------------
# Configuração centralizada (backend/config.py + env)
# ---------------------------------------------------------------------------

DIR_ENTREGAS = settings.dir_entregas
SUBDIR = settings.subdir_novas_proposicoes
MODELO_NOME = "MODELO A SER SEGUIDO.docx"

# Palavras-chave padrão: derivadas da matriz de inteligência Family Talks
# (segue sendo sob demanda — o operador aciona a rota para varrer).
KEYWORDS_PADRAO = family_talks.keywords_para_busca()

# URLs oficiais de detalhe/chamada por casa.
URL_CAMARA_BASE = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_CAMARA_HYPERLINK = "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id}"
URL_SENADO_HYPERLINK = "https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo}"

_HEADERS = {"Accept": "application/json"}

# Formatação oficial do modelo (extraída do .docx).
FONTE = "Montserrat"
TAMANHO_PT = 10
COR_TEXTO = RGBColor(0x33, 0x33, 0x33)  # 333333
COR_NUMERO = RGBColor(0xFF, 0x00, 0x00)  # ff0000


class ClippingError(Exception):
    """Erro de negócio com mensagem amigável."""


def _pasta_entregas() -> Path:
    pasta = DIR_ENTREGAS / SUBDIR
    return pasta


def _rotulo_arquivo_seguro(rotulo: Optional[str]) -> str:
    """Sanitiza o rótulo usado no nome do arquivo (impede path traversal).

    Mantém apenas letras/dígitos/espaços/_-. e remove `.`/`..` nas bordas; se
    sobrar vazio (ou apontar para fora), levanta ClippingError em vez de gravar
    fora da pasta de entregas (AGENTS.md: entregas sempre em dir_entregas).
    """
    bruto = (rotulo or _dt.date.today().isoformat()).strip()
    limpo = re.sub(r"[^0-9A-Za-zÀ-ú ._-]", "-", bruto).strip(" .")
    if not limpo or limpo in {".", ".."}:
        raise ClippingError("Período inválido para o nome do arquivo do Clipping.")
    return limpo


def _caminho_modelo() -> Path:
    caminho = settings.modelo_clipping
    if not caminho.exists():
        raise ClippingError(
            f"Modelo não encontrado em: {caminho}. Confirme que "
            "'MODELO A SER SEGUIDO.docx' está na pasta backend/templates/ "
            "do repositório."
        )
    return caminho


# ---------------------------------------------------------------------------
# Aquisição de dados (Câmara e Senado) — sob demanda
# ---------------------------------------------------------------------------

def _txt(valor: Any) -> str:
    if isinstance(valor, dict):
        return str(valor.get("nome") or valor.get("sigla") or "").strip()
    return "" if valor is None else str(valor).strip()


def _limpar_autor(nome: str) -> str:
    """Remove prefixos/cargos comuns deixados pelas APIs (ex: 'Dep. Fulano')."""
    return re.sub(r"^(Dep\.|Deputad[oa]|Parlamentar|Senador[a]?|Exm|Excel)\s+", "", nome).strip()


async def _enriquecer_camara_autor_data(
    client: httpx.AsyncClient, proposta: Dict[str, Any]
) -> Dict[str, Any]:
    """Acrescenta autor(es) e data de apresentação a uma proposição da Câmara."""
    pid = proposta.get("id")
    if not pid:
        return {"autor": None, "data": None}

    try:
        detalhe_resp, autores_resp = await asyncio.gather(
            client.get(f"{URL_CAMARA_BASE}/{pid}", headers=_HEADERS),
            client.get(f"{URL_CAMARA_BASE}/{pid}/autores", headers=_HEADERS),
        )
    except httpx.HTTPError:
        return {"autor": None, "data": None}

    detalhe = {}
    if detalhe_resp.status_code == 200:
        detalhe = (detalhe_resp.json() or {}).get("dados") or {}

    data_raw = detalhe.get("dataApresentacao")
    data = (
        _data_br(str(data_raw)[:10])
        if data_raw
        else None
    )

    autores: List[str] = []
    if autores_resp.status_code == 200:
        for a in (autores_resp.json() or {}).get("dados") or []:
            nome = _limpar_autor(_txt(a.get("nome")))
            if not nome:
                continue
            sigla = _txt(a.get("siglaPartido") or a.get("partido"))
            uf = _txt(a.get("siglaUf") or a.get("uf"))
            if sigla and uf:
                nome = f"{nome} - {sigla}/{uf}"
            elif sigla:
                nome = f"{nome} - {sigla}"
            autores.append(nome)
    # Fallback: o campo 'autor' do detalhe (quando presente).
    if not autores:
        nome = _limpar_autor(_txt(detalhe.get("autor")))
        if nome:
            autores.append(nome)

    return {"autor": ", ".join(autores) or None, "data": data}


async def _buscar_camara(keywords: List[str]) -> List[Dict[str, Any]]:
    """Busca proposições na Câmara por palavras-chave e enriquece autor/data."""
    resultados: Dict[int, Dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
        for kw in keywords:
            try:
                dados = await _listar_proposicoes(
                    siglaTipo=None, ano=None, keywords=kw,
                    itens=20, enriquecer=False,
                )
            except Exception:
                continue
            for prop in dados.get("proposicoes") or []:
                prop.update(await _enriquecer_camara_autor_data(client, prop))
                pid = prop.get("id")
                if pid is not None:
                    resultados.setdefault(pid, prop)

        # A lista da Câmara vem sem autor/data; erro individual não descarta o item.
        for prop in resultados.values():
            if not prop.get("autor"):
                prop.update(await _enriquecer_camara_autor_data(client, prop))

    lista = list(resultados.values())
    lista.sort(key=lambda p: str(p.get("numero") or ""))
    return lista


def _formatar_data_materia(materia: Dict[str, Any]) -> Optional[str]:
    """Normaliza a Data do Senado (pode vir como '2026-02-09' ou timestamp ISO)."""
    data = materia.get("data")
    if not data:
        return None
    texto = str(data).strip()
    parte = texto.split("T")[0]
    if re.match(r"^\d{4}-\d{2}-\d{2}$", parte):
        return parte
    return texto


def _data_br(valor: Any) -> Optional[str]:
    """Converte datas ISO ('2026-09-07') para o padrão da entrega DD/MM/AAAA."""
    texto = str(valor or "").strip()
    parte = texto.split("T")[0]
    encontro = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", parte)
    return f"{encontro.group(3)}/{encontro.group(2)}/{encontro.group(1)}" if encontro else (texto or None)


def _data_objeto(valor: Any) -> Optional[_dt.date]:
    """Converte 'DD/MM/AAAA' ou ISO ('2026-09-07'/timestamp) em date (comparável)."""
    texto = str(valor or "").strip().split("T")[0]
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return _dt.datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _dentro_janela(
    valor: Any,
    data_inicio: Optional[_dt.date] = None,
    data_fim: Optional[_dt.date] = None,
) -> bool:
    """True se a data da proposição estiver na janela [data_inicio, data_fim].

    Janela inclusiva nos dois extremos. Sem janela, retorna True (sem filtro).
    """
    if data_inicio is None and data_fim is None:
        return True
    data = _data_objeto(valor)
    if data is None:
        return False
    if data_inicio is not None and data < data_inicio:
        return False
    if data_fim is not None and data > data_fim:
        return False
    return True


async def _buscar_senado(keywords: List[str]) -> List[Dict[str, Any]]:
    """Busca matérias no Senado por palavras-chave (já traz autor, data e url)."""
    vistos: Dict[int, Dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as _client:
        for kw in keywords:
            dados = await _listar_materias_senado(
                sigla=None, ano=None, tramitando="S", enriquecer=False, keywords=kw,
            )
            for m in dados.get("materias") or []:
                codigo = m.get("codigo")
                if codigo is None:
                    continue
                m["autor"] = _limpar_autor(m.get("autor") or "") or m.get("autor")
                m["data"] = _formatar_data_materia(m)
                vistos.setdefault(codigo, m)

    lista = list(vistos.values())
    lista.sort(key=lambda m: str(m.get("numero") or ""))
    return lista


# ---------------------------------------------------------------------------
# Geração do documento (clonagem fiel do template)
# ---------------------------------------------------------------------------

def _add_rel_hyperlink(doc: Document, url: str) -> str:
    """Registra um relacionamento externo de hyperlink e devolve o rId."""
    return doc.part.relate_to(url, RT.HYPERLINK, is_external=True)


def _novo_run(par, text: str, *, bold: bool = False, color: RGBColor = COR_TEXTO,
              size: int = TAMANHO_PT, font: str = FONTE) -> Any:
    run = par.add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.bold = bold
    run.font.underline = False
    return run


def _novo_paragrafo(doc: Document, proto: Any, label: str, valor: str) -> Any:
    """Clona o <w:pPr> de um parágrafo de corpo (formatação fiel) e reescreve o texto."""
    p = copy.deepcopy(proto._p)
    for child in list(p):
        if child.tag in (qn("w:r"), qn("w:hyperlink")):
            p.remove(child)
    from docx.text.paragraph import Paragraph
    par = Paragraph(p, doc)

    if label:
        _novo_run(par, label, bold=True, color=COR_TEXTO)
    _novo_run(par, valor or "—", bold=False, color=COR_TEXTO)
    return par


def _proposicao_hiperlink(doc: Document, numero: str, url: str) -> OxmlElement:
    """Monta um <w:hyperlink> com o número em negrito vermelho (estilo do modelo)."""
    hyperlink = OxmlElement("w:hyperlink")
    r_id = _add_rel_hyperlink(doc, url)
    hyperlink.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    fontes = OxmlElement("w:rFonts")
    for attr in ("w:ascii", "w:cs", "w:eastAsia", "w:hAnsi"):
        fontes.set(qn(attr), FONTE)
    rPr.append(fontes)
    bold = OxmlElement("w:b"); bold.set(qn("w:val"), "1"); rPr.append(bold)
    boldCs = OxmlElement("w:bCs"); boldCs.set(qn("w:val"), "1"); rPr.append(boldCs)
    cor = OxmlElement("w:color"); cor.set(qn("w:val"), "ff0000"); rPr.append(cor)
    sz = OxmlElement("w:sz"); sz.set(qn("w:val"), str(TAMANHO_PT * 2)); rPr.append(sz)
    szCs = OxmlElement("w:szCs"); szCs.set(qn("w:val"), str(TAMANHO_PT * 2)); rPr.append(szCs)
    run.append(rPr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = numero
    run.append(t)
    hyperlink.append(run)
    return hyperlink


def _paragrafo_numero(doc: Document, proto_numero: Any, numero: str, url: str) -> Any:
    """Clona o parágrafo-número do protótipo (pPr fiel) e injeta o hyperlink."""
    p = copy.deepcopy(proto_numero._p)
    # Remove runs antigos (ficam só a formatação do pPr).
    for child in list(p):
        if child.tag in (qn("w:r"), qn("w:hyperlink")):
            p.remove(child)
    # Adiciona o hyperlink do número.
    p.append(_proposicao_hiperlink(doc, numero, url))
    from docx.text.paragraph import Paragraph
    return Paragraph(p, doc)


def _paragrafo_valor(doc: Document, proto: Any, label: str, valor: str) -> Any:
    """Clona um parágrafo de corpo e escreve 'Rótulo' (negrito) + valor."""
    return _novo_paragrafo(doc, proto, label, valor)


def _substituir_corpo(doc: Document, camara: List[Dict], senado: List[Dict]) -> None:
    """Recria o corpo mantendo os títulos de seção e clonando os protótipos."""
    # Localiza os índices dos títulos de seção.
    indice_titulo_camara = None
    indice_titulo_senado = None
    for i, p in enumerate(doc.paragraphs):
        if p.style and p.style.name == "Title":
            texto = p.text.strip().lower()
            if "câmara" in texto or "camara" in texto:
                indice_titulo_camara = i
            elif "senado" in texto:
                indice_titulo_senado = i

    if indice_titulo_camara is None or indice_titulo_senado is None:
        raise ClippingError(
            "Não foi possível localizar os cabeçalhos 'Câmara dos Deputados' e "
            "'Senado Federal' no modelo. Estrutura inesperada."
        )

    paras = doc.paragraphs

    # Protótipo do parágrafo de número e de corpo: usamos o primeiro bloco de
    # corpo da seção da Câmara (após o título). Se não houver, cria um genérico.
    proto_numero = None
    proto_corpo = None
    for i in range(indice_titulo_camara + 1, indice_titulo_senado):
        p = paras[i]
        texto = p.text.strip()
        if proto_numero is None and texto and re.match(r"^(PEC|PL|MPV|PDL|PRC|PLP)\s+\d", texto):
            proto_numero = paras[i]
            continue
        if proto_corpo is None and texto and texto.lower().startswith(("ementa", "autor", "data")):
            proto_corpo = paras[i]
            break

    if proto_numero is None:
        raise ClippingError("O modelo não possui um bloco de proposição reconhecível para clonar.")

    # Remove os parágrafos de corpo (entre os títulos e após o título do Senado),
    # preservando os dois títulos de seção.
    def _remover(par):
        par._p.getparent().remove(par._p)

    for i in range(len(paras) - 1, indice_titulo_camara, -1):
        if i == indice_titulo_senado:
            continue
        _remover(paras[i])

    # Após a remoção, os índices reordenam: Câmara segue em indice_titulo_camara
    # e o Senado passa a ser o parágrafo imediatamente seguinte.
    titulo_camara = doc.paragraphs[indice_titulo_camara]
    titulo_senado = doc.paragraphs[indice_titulo_camara + 1]

    # Insere os blocos da Câmara após o título da Câmara.
    _inserir_blocos(doc, titulo_camara, camara, proto_numero, proto_corpo, casa="camara")

    # Insere os blocos do Senado após o título do Senado.
    _inserir_blocos(doc, titulo_senado, senado, proto_numero, proto_corpo, casa="senado")


def _paragrafo_vazio(doc: Document, proto: Any) -> Any:
    """Clona um parágrafo de corpo vazio (espaçador entre blocos)."""
    p = copy.deepcopy(proto._p)
    for child in list(p):
        if child.tag in (qn("w:r"), qn("w:hyperlink")):
            p.remove(child)
    from docx.text.paragraph import Paragraph
    return Paragraph(p, doc)


def _inserir_blocos(doc: Document, ref_par: Any, itens: List[Dict], proto_numero: Any,
                    proto_corpo: Any, casa: str) -> None:
    """Insere, logo após ref_par, um bloco por proposição (número/ementa/autor/data),
    com um espaçador em branco entre blocos — replicando o modelo."""
    anterior = ref_par

    # O modelo tem um parágrafo em branco logo após cada título de seção.
    esp_inicial = _paragrafo_vazio(doc, proto_numero)
    anterior._p.addnext(esp_inicial._p)
    anterior = esp_inicial

    for indice, item in enumerate(itens):
        if indice > 0:
            esp = _paragrafo_vazio(doc, proto_numero)
            anterior._p.addnext(esp._p)
            anterior = esp

        if casa == "camara":
            numero = f"{item.get('siglaTipo')} {item.get('numero')}/{item.get('ano')}"
            url = URL_CAMARA_HYPERLINK.format(id=item.get("id"))
        else:
            numero = f"{item.get('sigla')} {item.get('numero')}/{item.get('ano')}"
            url = URL_SENADO_HYPERLINK.format(codigo=item.get("codigo"))
        ementa = item.get("ementa") or "—"
        autor = item.get("autor") or "—"
        data = _data_br(item.get("data")) or "—"

        p_num = _paragrafo_numero(doc, proto_numero, numero, url)
        anterior._p.addnext(p_num._p)
        anterior = p_num

        if proto_corpo is not None:
            # Ementa
            p_em = _paragrafo_valor(doc, proto_corpo, "Ementa: ", ementa)
            anterior._p.addnext(p_em._p); anterior = p_em
            # Autor
            p_aut = _paragrafo_valor(doc, proto_corpo, "Autor: ", autor)
            anterior._p.addnext(p_aut._p); anterior = p_aut
            # Data
            p_dat = _paragrafo_valor(doc, proto_corpo, "Data de apresentação: ", data)
            anterior._p.addnext(p_dat._p); anterior = p_dat
        else:
            # Fallback (sem protótipo): parágrafo único com tudo.
            p = _paragrafo_valor(doc, proto_numero, numero, ementa)
            anterior._p.addnext(p._p); anterior = p


async def _gerar_clipping_async(
    keywords: Optional[List[str]] = None,
    periodo: Optional[str] = None,
    ano: int = None,
    baixar: bool = False,
    aplicar_filtro_family_talks: bool = True,
    data_inicio: Optional[_dt.date] = None,
    data_fim: Optional[_dt.date] = None,
) -> Dict[str, Any]:
    """Núcleo assíncrono da geração (usado pela rota FastAPI).

    Filtro Inteligente Family Talks — POTENCIALIZADOR sob demanda (AGENTS.md:
    o operador aciona a rota; nada roda em segundo plano ou por agendamento):
        1. Busca bruta nas duas casas pelos termos da matriz;
        2. Janela estrita de apresentação [data_inicio, data_fim] (inclusiva) —
           apenas proposições novas do período pedido pelo operador;
        3. Cruzamento de cada ementa com os TEMAS PRIORITÁRIOS;
        4. Exclusão automática de pautas fora do escopo (jurídico de família,
           alienação parental, direito penal familiar e marcadores estranhos);
        5. Apenas as proposições relevantes seguem para o documento.
    """
    if data_inicio and data_fim and data_fim < data_inicio:
        raise ClippingError(
            "data_fim anterior a data_inicio. Informe uma janela válida "
            "(ex: 2026-08-31 a 2026-09-04)."
        )
    ano_atual = ano if ano is not None else _dt.date.today().year
    palavras = [kw for kw in (keywords or KEYWORDS_PADRAO) if kw and kw.strip()]
    if not palavras:
        placeholders = ", ".join(KEYWORDS_PADRAO[:5])
        raise ClippingError(f"Nenhuma palavra-chave informada. Use, ex: {placeholders}.")

    # Fail-fast: valida o template ANTES de acionar as APIs governamentais
    # (não queima rate-limit se o modelo estiver ausente — AGENTS.md).
    caminho_modelo = _caminho_modelo()

    # 1) Busca de dados (sob demanda).
    camara_bruta, senado_bruto = await asyncio.gather(
        _buscar_camara(palavras),
        _buscar_senado(palavras),
    )

    # 1b) Janela estrita de apresentação (filtro de datas do operador).
    total_camara = len(camara_bruta)
    total_senado = len(senado_bruto)
    camara_bruta = [c for c in camara_bruta if _dentro_janela(c.get("data"), data_inicio, data_fim)]
    senado_bruto = [s for s in senado_bruto if _dentro_janela(s.get("data"), data_inicio, data_fim)]
    fora_janela_camara = total_camara - len(camara_bruta)
    fora_janela_senado = total_senado - len(senado_bruto)

    # 2) Filtro de inteligência Family Talks.
    camara, senado = camara_bruta, senado_bruto
    if aplicar_filtro_family_talks:
        camara = family_talks.filtrar(camara_bruta)
        senado = family_talks.filtrar(senado_bruto)
        database.registrar_evento(
            "family_talks",
            f"Câmara {len(camara)}/{total_camara} aprovadas; Senado {len(senado)}/{total_senado} "
            f"aprovadas; {len(camara) + len(senado)} itens mantidos no relatório. "
            f"Janela: {data_inicio or '—'} a {data_fim or '—'} "
            f"(fora da janela: Câmara {fora_janela_camara}, Senado {fora_janela_senado}).",
        )

    # 3) Geração a partir do modelo (template já validado no início).
    doc = Document(str(caminho_modelo))
    _substituir_corpo(doc, camara, senado)

    # 4) Salvamento (nunca sobrescreve o modelo).
    rotulo = _rotulo_arquivo_seguro(periodo)
    nome = f"Clipping_Novas_Proposicoes_{rotulo}.docx"
    destino = _pasta_entregas() / nome
    doc.save(str(destino))

    filtro = {
        "matriz": "Family Talks",
        "ativos": aplicar_filtro_family_talks,
        "temas_detectados": family_talks.temas_detectados(camara + senado),
        "descartados_camara": (total_camara - len(camara)) - fora_janela_camara,
        "descartados_senado": (total_senado - len(senado)) - fora_janela_senado,
        "descartados_total": (total_camara - len(camara)) + (total_senado - len(senado))
        - fora_janela_camara - fora_janela_senado,
        "fora_janela_camara": fora_janela_camara,
        "fora_janela_senado": fora_janela_senado,
        "data_inicio": data_inicio.isoformat() if data_inicio else None,
        "data_fim": data_fim.isoformat() if data_fim else None,
    }

    return {
        "arquivo": nome,
        "caminho": str(destino),
        "periodo": rotulo,
        "palavras_chave": palavras,
        "ano": ano_atual,
        "total_camara": len(camara),
        "total_senado": len(senado),
        "total": len(camara) + len(senado),
        "modelo": str(caminho_modelo),
        "camara": camara,
        "senado": senado,
        "filtro": filtro,
    }


def gerar_clipping(
    keywords: Optional[List[str]] = None,
    periodo: Optional[str] = None,
    ano: int = None,
    baixar: bool = False,
    aplicar_filtro_family_talks: bool = True,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> Dict[str, Any]:
    """Wrapper síncrono (CLI/scripts). Para uso assíncrono, chame _gerar_clipping_async."""
    return asyncio.run(_gerar_clipping_async(
        keywords=keywords, periodo=periodo, ano=ano, baixar=baixar,
        aplicar_filtro_family_talks=aplicar_filtro_family_talks,
        data_inicio=_data_objeto(data_inicio), data_fim=_data_objeto(data_fim),
    ))


# ---------------------------------------------------------------------------
# Rota FastAPI
# ---------------------------------------------------------------------------

from fastapi import APIRouter, HTTPException, Query
from starlette.requests import Request

from rate_limit import limiter, LIMITE_DOU  # limite conservador por requisição pesada

router = APIRouter(prefix="/api/exportar", tags=["Exportação — Clipping Semanal"])


@router.post("/clipping-semanal")
@limiter.limit("5/minute")
async def gerar_clipping_rota(
    request: Request,
    keywords: Optional[str] = Query(None, max_length=500, description="Palavras-chave separadas por vírgula. Vazio = matriz Family Talks"),
    periodo: Optional[str] = Query(None, description="Rótulo do arquivo (ex: 2026-09-06)"),
    baixar: bool = Query(False, description="True = retorna o .docx como download"),
    sem_filtro: bool = Query(False, description="True = desativa o Filtro Inteligente Family Talks (varrredura bruta)"),
    data_inicio: Optional[str] = Query(None, description="Início da janela de apresentação (ISO: 2026-08-31)"),
    data_fim: Optional[str] = Query(None, description="Fim da janela de apresentação (ISO: 2026-09-04)"),
    incluir_dados: bool = Query(False, description="True = inclui as listas estruturadas de proposições (câmara/senado) na resposta JSON"),
):
    """
    Gera o Clipping de Novas Proposições (sob demanda — conforme AGENTS.md).

    - Busca Câmara (PEC, PLP, PL, MPV, PDC) e Senado pelos temas da matriz
      Family Talks e cruza cada ementa com os TEMAS PRIORITÁRIOS.
    - data_inicio/data_fim: filtro estrito e inclusivo pela data de
      apresentação (apenas proposições NOVAS daquele período).
    - Exclui automaticamente pautas fora do escopo (jurídico de família,
      alienação parental, direito penal familiar e marcadores estranhos).
    - Formato de entrega: Identificação (ex: PL 595/2024), Ementa, Autor
      (nome e UF) e Data de apresentação (DD/MM/AAAA).
    - Clona o 'MODELO A SER SEGUIDO.docx' preservando a formatação original.
    """
    lista_palavras = None
    if keywords:
        lista_palavras = [k.strip() for k in keywords.split(",") if k.strip()]

    try:
        resultado = await _gerar_clipping_async(
            keywords=lista_palavras, periodo=periodo,
            aplicar_filtro_family_talks=not sem_filtro,
            data_inicio=_data_objeto(data_inicio),
            data_fim=_data_objeto(data_fim),
        )
    except ClippingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not baixar:
        resumo = {
            "arquivo": resultado["arquivo"],
            "periodo": resultado["periodo"],
            "palavras_chave": resultado["palavras_chave"],
            "total_camara": resultado["total_camara"],
            "total_senado": resultado["total_senado"],
            "total": resultado["total"],
            "modelo": resultado["modelo"],
            "filtro_family_talks": resultado["filtro"],
        }
        if incluir_dados:
            resumo["camara"] = resultado["camara"]
            resumo["senado"] = resultado["senado"]
        return resumo

    from fastapi.responses import FileResponse
    caminho = resultado["caminho"]
    if not os.path.exists(caminho):
        raise HTTPException(status_code=500, detail="Arquivo não pôde ser gravado em disco.")
    return FileResponse(
        caminho,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        filename=resultado["arquivo"],
    )
