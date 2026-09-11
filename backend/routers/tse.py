"""
Módulo de Inteligência Eleitoral (TSE — DivulgaCandContas).

Consulta candidaturas e o detalhamento patrimonial dos candidatos
usando a API aberta do Tribunal Superior Eleitoral de forma assíncrona (httpx).

Contrato oficial (API pública do DivulgaCandContas):
    GET /candidatura/listar/{ano}/{municipio}/{eleicao}/{cargo}/candidatos
    GET /candidatura/buscar/{ano}/{municipio}/{eleicao}/candidato/{candidato}
    GET /eleicao/uf/{sqEleicao}/{uf}/municipios

Em eleições MUNICIPAIS (2024, 2020) o segmento {municipio} é o CÓDIGO do
município (ex.: 71072 = São Paulo). Em eleições GERAIS (2022) o segmento é a
UF (ex.: SP). O valor "BR" só é válido para cargos nacionais na eleição geral.
"""
from fastapi import APIRouter, HTTPException, Path, Query
from starlette.requests import Request
from typing import Optional
import httpx

from rate_limit import limiter, LIMITE_TSE
from config import settings

router = APIRouter(prefix="/tse", tags=["TSE"])

# Base oficial da API pública do DivulgaCandContas. Sobreponível em deploys
# (ex.: proxy/cache autorizado) via env TSE_BASE_URL (config.py).
BASE_TSE = settings.tse_base_url

# Portal oficial para consulta manual de qualquer candidatura.
PORTAL_TSE = settings.tse_portal_url

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": settings.tse_user_agent,
}

# Ano eleitoral -> id_eleicao correspondente (ID oficial do DivulgaCandContas).
# O código de 2026 vem de settings.tse_id_eleicao_2026 (confirme antes de operar).
ID_ELEICAO_MUNICIPAL = {
    2024: "2045202024",
    2020: "2030402020",
}
ID_ELEICAO_GERAL = {
    2026: settings.tse_id_eleicao_2026,
    2022: "2040602022",
    2018: "2022802018",
}
IDS_ELEICAO = {**ID_ELEICAO_MUNICIPAL, **ID_ELEICAO_GERAL}

# Cargos reconhecidos pela API (códigos oficiais do TSE).
CARGOS_MUNICIPAIS = {
    11: "Prefeito",
    12: "Vice-Prefeito",
    13: "Vereador",
}
CARGOS_GERAIS = {
    1: "Presidente",
    2: "Vice-Presidente",
    3: "Governador",
    4: "Vice-Governador",
    5: "Senador",
    6: "Deputado Federal",
    7: "Deputado Estadual/Distrital",
}
CARGOS = {**CARGOS_GERAIS, **CARGOS_MUNICIPAIS}

_TIMEOUT = httpx.Timeout(settings.tse_timeout)


def _id_eleicao(ano: int) -> str:
    """Retorna o id_eleicao oficial do TSE para o ano ou levanta 400."""
    id_eleicao = IDS_ELEICAO.get(ano)
    if not id_eleicao:
        suportados = ", ".join(str(a) for a in sorted(IDS_ELEICAO))
        raise HTTPException(
            status_code=400,
            detail=f"Ano inválido: {ano}. Anos suportados: {suportados}.",
        )
    return id_eleicao


def _validar_uf(uf: str, liberar_br: bool) -> str:
    uf = (uf or "").strip().upper()
    if uf == "BR" and liberar_br:
        return uf
    if len(uf) != 2 or not uf.isalpha():
        raise HTTPException(
            status_code=400,
            detail="UF inválida: informe a sigla de 2 letras (ex: SP).",
        )
    return uf


def _validar_cargo(codigo_cargo: int, municipais: bool) -> int:
    tabela = CARGOS_MUNICIPAIS if municipais else CARGOS_GERAIS
    nome = tabela.get(codigo_cargo)
    if not nome:
        cargos = ", ".join(f"{k} ({v})" for k, v in tabela.items())
        raise HTTPException(
            status_code=400,
            detail=f"Cargo inválido ({'municipal' if municipais else 'geral'}): {codigo_cargo}. Cargos: {cargos}.",
        )
    return codigo_cargo


def _eh_municipal(ano: int) -> bool:
    return ano in ID_ELEICAO_MUNICIPAL


def _texto(valor) -> str:
    """Normaliza valores que o TSE retorna como objeto {codigo, nome} ou string."""
    if isinstance(valor, dict):
        return str(valor.get("nome") or valor.get("sigla") or "").strip()
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        # Datas de nascimento vêm como timestamp em milissegundos (epoch).
        if valor > 1_000_000_000_000:
            import datetime

            try:
                return datetime.datetime.fromtimestamp(valor / 1000).strftime("%Y-%m-%d")
            except (OverflowError, ValueError, OSError):
                return ""
        return str(valor)
    return str(valor or "").strip()


def _enriquece(c: dict) -> dict:
    """Extrai de forma defensiva os campos adicionais que a API do TSE
    pode ou não entregar na listagem (varia por ano/versão do DivulgaCandContas)."""
    partido = c.get("partido") or {}
    cargo = c.get("cargo") or {}

    municipio_raw = c.get("municipio") or c.get("localCandidatura") or c.get("nmUe")
    codigo_municipio = None
    nome_municipio = None
    if isinstance(municipio_raw, dict):
        codigo_municipio = municipio_raw.get("codigo") or municipio_raw.get("id")
        nome_municipio = municipio_raw.get("nome")
    else:
        nome_municipio = municipio_raw

    return {
        "id": c.get("id"),
        "nomeUrna": c.get("nomeUrna"),
        "nomeCompleto": c.get("nomeCompleto"),
        "numero": c.get("numero"),
        "siglaPartido": partido.get("sigla") or c.get("siglaPartido"),
        "descricaoSituacao": _texto(c.get("descricaoSituacao") or c.get("situacao")),
        "fotoUrl": c.get("fotoUrl"),
        "municipio": nome_municipio,
        "codigoMunicipio": codigo_municipio,
        "cpf": c.get("cpf"),
        "cnpj": c.get("cnpj") or c.get("cnpjcampanha"),
        "genero": _texto(c.get("genero") or c.get("descricaoSexo")),
        "corRaca": _texto(c.get("corRaca") or c.get("descricaoCorRaca") or c.get("raca")),
        "dataNascimento": _texto(c.get("dataNascimento") or c.get("dataDeNascimento")),
        "codigoCargo": cargo.get("codigo") or c.get("codigoCargo") or c.get("cargo"),
    }


def _aplica_filtro(candidatos: list, valor: str, campo: str) -> list:
    """Filtro por substring (case-insensitive) em um campo do candidato."""
    alvo = _texto(valor).lower()
    if not alvo:
        return candidatos
    return [
        c for c in candidatos
        if alvo in _texto(c.get(campo)).lower()
    ]


async def _get_json(url: str) -> dict:
    """Executa o GET na API do TSE e devolve o JSON ou erro amigável."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resposta = await client.get(url, headers=_HEADERS)
            resposta.raise_for_status()
            return resposta.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 403:
            raise HTTPException(
                status_code=502,
                detail=f"O TSE bloqueou a consulta automática (403 Access Denied). Confirme os dados diretamente no portal {PORTAL_TSE} e tente novamente em instantes.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail=f"O TSE respondeu com erro {status} na consulta.",
        ) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="O TSE demorou demais para responder. Tente novamente.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Não foi possível se comunicar com a API do TSE.") from exc


@router.get("/municipios")
@limiter.limit(LIMITE_TSE)
async def listar_municipios(
    request: Request,
    ano: int = Query(..., description="Ano eleitoral: 2020, 2022 ou 2024", ge=2018, le=2100),
    uf: str = Query(..., min_length=2, max_length=2, description="Sigla da UF (2 letras)"),
):
    """
    Lista os municípios de uma UF que participam da eleição informada.
    Necessário para montar a consulta de candidatos em eleições municipais.
    """
    id_eleicao = _id_eleicao(ano)
    uf = _validar_uf(uf, liberar_br=False)

    url = f"{BASE_TSE}/eleicao/uf/{id_eleicao}/{uf}/municipios"
    dados = await _get_json(url)

    if isinstance(dados, list):
        itens = dados
    else:
        itens = (
            dados.get("municipios")
            or dados.get("unidadesEleitorais")
            or dados.get("resultado")
            or []
        )

    municipios = []
    for m in itens:
        if isinstance(m, dict):
            codigo = m.get("codigo") or m.get("id") or m.get("sqUe")
            nome = m.get("nome") or m.get("descricao") or m.get("nmUe")
            if codigo is not None and nome:
                municipios.append({"codigo": str(codigo), "nome": str(nome)})

    municipios.sort(key=lambda x: x["nome"].lower())
    return {"total": len(municipios), "municipios": municipios}


@router.get("/candidatos")
@limiter.limit(LIMITE_TSE)
async def listar_candidatos(
    request: Request,
    ano: int = Query(..., description="Ano eleitoral: 2020, 2022 ou 2024", ge=2018, le=2100),
    uf: str = Query(..., min_length=2, max_length=2, description="Sigla da UF (2 letras) ou BR apenas para cargos nacionais"),
    codigo_cargo: int = Query(..., ge=1, le=13, description="Código do cargo (1, 3, 5, 6, 7, 11, 12, 13)"),
    municipio: Optional[str] = Query(None, max_length=12, description="Código do município (obrigatório nas eleições municipais, ex: 71072)"),
    q: Optional[str] = Query(None, max_length=120, description="Termo livre para filtrar por nome de urna, nome completo, CPF/CNPJ, número ou partido"),
    partido: Optional[str] = Query(None, max_length=20, description="Sigla do partido (ex: PL) para filtrar candidaturas"),
    situacao: Optional[str] = Query(None, max_length=40, description="Status da candidatura (ex: deferido, indeferido, sub judice)"),
):
    """
    Lista candidatos de uma eleição com base em ano, UF/código de município,
    cargo e filtros avançados (normalizado para o contrato oficial do TSE).
    """
    id_eleicao = _id_eleicao(ano)
    municipais = _eh_municipal(ano)
    uf = _validar_uf(uf, liberar_br=not municipais)
    _validar_cargo(codigo_cargo, municipais=municipais)

    # Eleições municipais exigem o CÓDIGO do município na rota (não aceita "BR"/UF).
    unidade = uf
    if municipais:
        if not municipio:
            raise HTTPException(
                status_code=400,
                detail="Nas eleições municipais (2020/2024) informe o código do município. Use o seletor de município na aba Eleições.",
            )
        unidade = municipio
    else:
        # Cargo nacional (Presidente) é o único que aceita "BR" na eleição geral.
        if uf == "BR" and codigo_cargo != 1:
            raise HTTPException(
                status_code=400,
                detail="A unidade 'BR' só é válida para o cargo de Presidente. Selecione uma UF para os demais cargos.",
            )

    url = f"{BASE_TSE}/candidatura/listar/{ano}/{unidade}/{id_eleicao}/{codigo_cargo}/candidatos"
    dados = await _get_json(url)

    candidatos_brutos = dados.get("candidatos") or []

    candidatos = [_enriquece(c) for c in candidatos_brutos]

    # Filtros avançados aplicados de forma defensiva: os campos podem não
    # estar presentes em algumas versões da API; quando ausentes, o filtro
    # apenas não tem efeito (não derruba a consulta).
    if partido:
        candidatos = [c for c in candidatos if _texto(c["siglaPartido"]).upper() == partido.strip().upper()] or _aplica_filtro(candidatos, partido, "siglaPartido")
    candidatos = _aplica_filtro(candidatos, situacao, "descricaoSituacao")

    if q:
        termo = q.strip().lower()
        if termo:
            campos = ["nomeUrna", "nomeCompleto", "siglaPartido", "municipio", "cpf", "cnpj"]
            candidatos = [
                c for c in candidatos
                if any(termo in _texto(c.get(campo)).lower() for campo in campos)
                or termo == str(c.get("numero") or "").lower()
            ]

    return {
        "total": len(candidatos),
        "candidatos": candidatos,
        "consulta": {"ano": ano, "uf": uf, "municipio": municipio, "cargo": codigo_cargo},
    }


@router.get("/candidato/{ano}/{uf}/{id_candidato}")
@limiter.limit(LIMITE_TSE)
async def detalhe_candidato(
    request: Request,
    ano: int = Path(..., ge=2018, le=2100),
    uf: str = Path(..., min_length=2, max_length=2),
    id_candidato: int = Path(..., ge=1),
    municipio: Optional[str] = Query(None, max_length=12, description="Código do município (obrigatório nas eleições municipais, ex: 71072)"),
):
    """Detalha um candidato, incluindo dados pessoais, eleição e patrimônio declarado."""
    id_eleicao = _id_eleicao(ano)
    municipais = _eh_municipal(ano)
    uf = _validar_uf(uf, liberar_br=not municipais)

    unidade = uf
    if municipais:
        if not municipio:
            raise HTTPException(
                status_code=400,
                detail="Nas eleições municipais informe o código do município para abrir o dossiê.",
            )
        unidade = municipio

    url = f"{BASE_TSE}/candidatura/buscar/{ano}/{unidade}/{id_eleicao}/candidato/{id_candidato}"
    dados = await _get_json(url)

    # O TSE pode wrapperar a resposta em 'candidato'/'dadosCandidato' ou devolver
    # o objeto Candidato diretamente na raiz.
    conteudo = dados.get("candidato") or dados.get("dadosCandidato") or dados

    if isinstance(conteudo, dict) and "candidato" in conteudo:
        conteudo = conteudo["candidato"]

    partido = conteudo.get("partido") or {}
    coligacao = conteudo.get("coligacao")
    federacao = conteudo.get("federacao")

    bens_brutos = conteudo.get("bens") or dados.get("bens") or []
    if isinstance(bens_brutos, dict):
        bens_brutos = bens_brutos.get("bens") or []

    bens = []
    for bem in bens_brutos:
        if not isinstance(bem, dict):
            continue
        # Campos oficiais: descricao, descricaoDeTipoDeBem, valor (números).
        valor_raw = bem.get("valor")
        try:
            valor = round(float(valor_raw or 0), 2)
        except (TypeError, ValueError):
            valor = 0.0
        bens.append(
            {
                "tipo": _texto(bem.get("descricaoDeTipoDeBem") or bem.get("tipoBem") or bem.get("descricaoTipoBem")),
                "descricao": str(
                    bem.get("descricao")
                    or bem.get("descricaoDetalhadaBem")
                    or bem.get("descricaoBem")
                    or ""
                ).strip(),
                "valor": valor,
            }
        )

    total_bens = conteudo.get("totalDeBens")
    try:
        total_bens = round(float(total_bens or sum(b["valor"] for b in bens)), 2)
    except (TypeError, ValueError):
        total_bens = round(sum(b["valor"] for b in bens), 2)

    return {
        "dados": {
            "nomeCompleto": conteudo.get("nomeCompleto"),
            "nomeUrna": conteudo.get("nomeUrna"),
            "cpf": conteudo.get("cpf"),
            "ocupacao": _texto(conteudo.get("ocupacao")),
            "grauInstrucao": _texto(conteudo.get("grauInstrucao")),
            "genero": _texto(conteudo.get("genero") or conteudo.get("descricaoSexo")),
            "corRaca": _texto(conteudo.get("corRaca") or conteudo.get("descricaoCorRaca") or conteudo.get("raca")),
            "dataNascimento": _texto(conteudo.get("dataNascimento") or conteudo.get("dataDeNascimento")),
            "estadoCivil": _texto(conteudo.get("estadoCivil") or conteudo.get("descricaoEstadoCivil")),
            "situacao": _texto(conteudo.get("descricaoSituacao") or conteudo.get("situacao")),
            "fotoUrl": conteudo.get("fotoUrl"),
        },
        "eleicao": {
            "partido": partido.get("sigla"),
            "numero": conteudo.get("numero"),
            "coligacao": _texto(coligacao) if isinstance(coligacao, dict) else str(coligacao or "").strip() or None,
            "federacao": _texto(federacao) if isinstance(federacao, dict) else str(federacao or "").strip() or None,
        },
        "patrimonio": {
            "totalDeBens": total_bens,
            "bens": bens,
        },
    }