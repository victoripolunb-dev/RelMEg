"""
Extrator de dados eleitorais do TSE (DivulgaCandContas) pronto para BI.

Fluxo:
    1. Consulta a listagem de candidatos da API pública do TSE.
    2. Enriquecimento: para cada candidato busca o detalhe aprofundado
       (ocupação, gênero, raça, total de bens declarados e situação do deferimento).
    3. Formatação: estrutura tudo em um DataFrame limpo (pandas).
    4. Exportação: grava um .xlsx no diretório de entregas 'RelMeg - Entregas/TSE'
       e disponibiliza o arquivo como download (funciona em qualquer deploy).

Confiabilidade:
    - Execução estritamente sob demanda (acionada por rota/CLI, nunca em background).
    - Enriquecimento paralelo via asyncio.gather (apenas concorrência HTTP dentro
      de uma única requisição on-demand, conforme AGENTS.md).
    - Tratamento de erros por candidato: uma falha pontual não derruba a extração
      (o candidato é mantido com o que a listagem já forneceu).
    - Normalização defensiva de valores (objetos {codigo, nome}, timestamps, etc.).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import io
import random
import re
import unicodedata as _ud
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pandas as pd
from loguru import logger

import database
from config import settings
from servicos.modelo_base import bytes_com_gabarito, gabarito_modelo, gravar_com_gabarito

# ---------------------------------------------------------------------------
# Configuração centralizada (backend/config.py + env)
# ---------------------------------------------------------------------------

# Base oficial da API pública do DivulgaCandContas. Sobreponível em deploys
# (ex.: proxy/cache autorizado). Mantém paridade com routers/tse.py.
BASE_TSE = settings.tse_base_url
PORTAL_TSE = settings.tse_portal_url
SUBDIR_TSE = settings.subdir_tse

# Janela de validade do cache local (dias/horas via settings / TSE_CACHE_TTL).
TSE_CACHE_TTL = settings.tse_cache_ttl

# Regra de redação de mensagens de erro (A3): remove caminhos absolutos do
# filesystem e URLs internas antes de gravar no campo `detalhe`/auditoria,
# para nunca expor estrutura local de disco ao operador ou em logs.
_RE_REDIGE_CAMINHO = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s;,\"']*|~[\\/][^\s;,\"']*|/(?:home|Users|tmp|var|opt)[/\w.-]*)"
)


def _redigir_mensagem(mensagem: str) -> str:
    """Remove caminhos absolutos de uma mensagem de erro (leak de estrutura)."""
    texto = str(mensagem)
    return _RE_REDIGE_CAMINHO.sub("[caminho]", texto)

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": settings.tse_user_agent,
}

_TIMEOUT = httpx.Timeout(settings.tse_timeout)
# Concorrência máxima no enriquecimento (rate-limit das APIs públicas).
MAX_CONCORRENCIA = settings.tse_max_concurrency

# IDs oficiais de eleição por ano (mesmos do routers/tse.py). O código de 2026
# é configurável (settings.tse_id_eleicao_2026) — confirme antes de operar.
ID_ELEICAO_MUNICIPAL = {2024: "2045202024", 2020: "2030402020"}
ID_ELEICAO_GERAL = {
    2026: settings.tse_id_eleicao_2026,
    2022: "2040602022",
    2018: "2022802018",
}
IDS_ELEICAO = {**ID_ELEICAO_MUNICIPAL, **ID_ELEICAO_GERAL}

CARGOS_GERAIS = {
    1: "Presidente",
    2: "Vice-Presidente",
    3: "Governador",
    4: "Vice-Governador",
    5: "Senador",
    6: "Deputado_Federal",
    7: "Deputado_Estadual",
}
CARGOS_MUNICIPAIS = {
    11: "Prefeito",
    12: "Vice-Prefeito",
    13: "Vereador",
}
CARGOS = {**CARGOS_GERAIS, **CARGOS_MUNICIPAIS}


class ExtrairTSEError(Exception):
    """Erro de negócio ao extrair dados do TSE com mensagem amigável."""


# ---------------------------------------------------------------------------
# Normalização de valores
# ---------------------------------------------------------------------------

def _texto(valor: Any) -> str:
    """Normaliza valores que o TSE retorna como objeto/dict, timestamp ou string."""
    if isinstance(valor, dict):
        return str(valor.get("nome") or valor.get("sigla") or "").strip()
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if valor > 1_000_000_000_000:  # timestamp em ms (data de nascimento)
            import datetime

            try:
                return datetime.datetime.fromtimestamp(valor / 1000).strftime("%Y-%m-%d")
            except (OverflowError, ValueError, OSError):
                return ""
        return str(valor)
    return "" if valor is None else str(valor).strip()


def _texto_or_none(valor: Any) -> Optional[str]:
    texto = _texto(valor)
    return texto or None


def _numero_float(valor: Any) -> float:
    """Converte para float com tolerância a string/None. Retorna 0.0 em falha."""
    if valor is None:
        return 0.0
    try:
        return round(float(valor), 2)
    except (TypeError, ValueError):
        return 0.0


def _eh_municipal(ano: int) -> bool:
    return ano in ID_ELEICAO_MUNICIPAL


def _id_eleicao(ano: int) -> str:
    id_eleicao = IDS_ELEICAO.get(ano)
    if not id_eleicao:
        suportados = ", ".join(str(a) for a in sorted(IDS_ELEICAO))
        raise ExtrairTSEError(
            f"Ano eleitoral não suportado: {ano}. Anos suportados: {suportados}."
        )
    return id_eleicao


def _validar_uf(uf: str) -> str:
    uf = (uf or "").strip().upper()
    if len(uf) != 2 or not uf.isalpha():
        raise ExtrairTSEError(f"UF inválida: '{uf}'. Informe a sigla de 2 letras (ex: AL).")
    return uf


def _validar_cargo(codigo_cargo: int, municipais: bool) -> int:
    tabela = CARGOS_MUNICIPAIS if municipais else CARGOS_GERAIS
    if codigo_cargo not in tabela:
        cargos = ", ".join(f"{k} ({v})" for k, v in tabela.items())
        raise ExtrairTSEError(
            f"Cargo inválido ({'municipal' if municipais else 'geral'}): {codigo_cargo}. Cargos: {cargos}."
        )
    return codigo_cargo


def _nome_cargo(codigo_cargo: int) -> str:
    return CARGOS.get(codigo_cargo, f"Cargo_{codigo_cargo}")


# ---------------------------------------------------------------------------
# Camada HTTP (assíncrona) — retry com Exponential Backoff + Jitter
# ---------------------------------------------------------------------------

# Códigos HTTP transitórios que merecem nova tentativa: bloqueios temporários
# (403/429) e instabilidades de servidor (5xx). Erros 4xx definitivos (400,
# 404, 422...) não são repetidos.
_CODIGOS_RETRYAVEIS = {403, 429, 500, 502, 503, 504}


def _atraso_tentativa(tentativa: int) -> float:
    """Backoff exponencial (1s, 2s, 4s...) + jitter aleatório.

    O jitter suaviza o momento de reenvio entre requisições simultâneas
    (evita rajadas em sincronia contra a API pública do TSE).
    """
    expoente = max(1, tentativa) - 1
    exponencial = settings.tse_backoff_base * (2 ** expoente)
    return exponencial + random.uniform(0, settings.tse_backoff_jitter)


def _erro_final(ultimo_erro: Optional[Exception]) -> ExtrairTSEError:
    """Converte o último erro em mensagem amigável após esgotar as tentativas."""
    if isinstance(ultimo_erro, httpx.HTTPStatusError):
        status = ultimo_erro.response.status_code
        if status == 403:
            return ExtrairTSEError(
                "O TSE bloqueou a consulta automática (403 Access Denied). "
                f"Confirme os dados no portal {PORTAL_TSE} e tente novamente em instantes."
            )
        return ExtrairTSEError(f"O TSE respondeu com erro {status} na consulta.")
    if isinstance(ultimo_erro, httpx.TimeoutException):
        return ExtrairTSEError("O TSE demorou demais para responder. Tente novamente.")
    return ExtrairTSEError("Não foi possível se comunicar com a API do TSE.")


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    max_tentativas: Optional[int] = None,
) -> Dict[str, Any]:
    """GET na API do TSE com retry (Exponential Backoff + Jitter).

    Tentativas: falhas de conexão, timeout e códigos transitórios
    (403/429/5xx) são repetidos até ``max_tentativas`` (padrão
    settings.tse_max_tentativas) com espera crescente + jitter.
    Após esgotar, derruba ExtrairTSEError com mensagem amigável.
    """
    max_tentativas = max_tentativas if (max_tentativas or 0) > 0 else settings.tse_max_tentativas
    ultimo_erro: Optional[Exception] = None
    for tentativa in range(1, max_tentativas + 1):
        tenta_divoltar = False
        try:
            resposta = await client.get(url, headers=_HEADERS)
            resposta.raise_for_status()
            return resposta.json()
        except httpx.HTTPStatusError as exc:
            ultimo_erro = exc
            tenta_divoltar = exc.response.status_code in _CODIGOS_RETRYAVEIS
        except httpx.TransportError as exc:  # conexão, timeout, leitura/escrita
            ultimo_erro = exc
            tenta_divoltar = True

        if not tenta_divoltar or tentativa == max_tentativas:
            break
        await asyncio.sleep(_atraso_tentativa(tentativa))

    raise _erro_final(ultimo_erro)


# ---------------------------------------------------------------------------
# Enriquecimento individual
# ---------------------------------------------------------------------------

def _norm_listagem(c: Dict[str, Any], cargo_nome: str) -> Dict[str, Any]:
    """Extrai os campos disponíveis já na listagem (fallback caso o detalhe falhe)."""
    cargo = c.get("cargo") or {}
    partido = c.get("partido") or {}
    municipio_raw = c.get("municipio") or c.get("localCandidatura") or c.get("nmUe")
    codigo_municipio = None
    nome_municipio = None
    if isinstance(municipio_raw, dict):
        codigo_municipio = municipio_raw.get("codigo") or municipio_raw.get("id")
        nome_municipio = municipio_raw.get("nome")
    else:
        nome_municipio = municipio_raw

    return {
        "id_candidato": c.get("id"),
        "nome_urna": _texto_or_none(c.get("nomeUrna")),
        "nome_completo": _texto_or_none(c.get("nomeCompleto")),
        "numero": c.get("numero"),
        "partido_sigla": _texto_or_none(partido.get("sigla") or c.get("siglaPartido")),
        "cargo": _texto_or_none(cargo.get("nome") or cargo_nome),
        "uf": _texto_or_none(c.get("siglaUf") or c.get("uf")),
        "municipio": _texto_or_none(nome_municipio),
        "codigo_municipio": codigo_municipio,
        "cpf": _texto_or_none(c.get("cpf")),
        "cnpj": _texto_or_none(c.get("cnpj") or c.get("cnpjcampanha")),
        "situacao": _texto_or_none(c.get("descricaoSituacao") or c.get("situacao")),
        "genero": _texto_or_none(c.get("genero") or c.get("descricaoSexo")),
        "cor_raca": _texto_or_none(
            c.get("corRaca") or c.get("descricaoCorRaca") or c.get("raca")
        ),
    }


def _norm_detalhe(c: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai campos do endpoint de detalhe do candidato (enriquecimento)."""
    partido = c.get("partido") or {}
    coligacao = c.get("coligacao")
    return {
        "ocupacao": _texto_or_none(c.get("ocupacao")),
        "grau_instrucao": _texto_or_none(c.get("grauInstrucao")),
        "estado_civil": _texto_or_none(
            c.get("estadoCivil") or c.get("descricaoEstadoCivil")
        ),
        "data_nascimento": _texto_or_none(
            c.get("dataNascimento") or c.get("dataDeNascimento")
        ),
        "foto_url": _texto_or_none(c.get("fotoUrl")),
        "coligacao": _texto_or_none(coligacao) if isinstance(coligacao, dict) else _texto_or_none(coligacao),
        "situacao_detalhe": _texto_or_none(
            c.get("descricaoSituacao") or c.get("situacao")
        ),
        "partido_detalhe": _texto_or_none(partido.get("sigla")),
    }


# Blocos opcionais do detalhe rico (o "máximo" por candidato). Sem campos = tudo.
BLOCOS_DETALHE = ("dados", "bens", "propostas", "redes")


def _normalizar_campos_detalhe(campos: Optional[str]) -> Optional[set]:
    """Normaliza o parâmetro ``campos`` da extração (None = coleta o máximo).

    ``campos=bens,propostas`` restringe o que é persistido/lido por candidato.
    """
    if campos is None:
        return None
    tokens = {c.strip().lower() for c in str(campos).split(",") if c.strip()}
    desconhecidos = tokens - set(BLOCOS_DETALHE)
    if desconhecidos:
        raise ExtrairTSEError(
            "Blocos inválidos: " + ", ".join(sorted(desconhecidos))
            + ". Blocos disponíveis: " + ", ".join(BLOCOS_DETALHE) + "."
        )
    return tokens or None


def _plataforma_rede(url: Optional[str]) -> Optional[str]:
    """Identifica a plataforma a partir do domínio da URL da rede social.

    Zero chamadas externas: apenas heurística local sobre o host da URL.
    """
    texto = str(url or "").strip().lower()
    if not texto:
        return None
    marcas = (
        ("instagram", "Instagram"),
        ("facebok", "Facebook"),
        ("facebook", "Facebook"),
        ("fb.com", "Facebook"),
        ("x.com", "X (Twitter)"),
        ("twitter", "X (Twitter)"),
        ("youtube", "YouTube"),
        ("tiktok", "TikTok"),
        ("linkedin", "LinkedIn"),
        ("threads", "Threads"),
        ("whatsapp", "WhatsApp"),
        ("telegram", "Telegram"),
        ("site", "Site próprio"),
    )
    for marca, rotulo in marcas:
        if marca in texto:
            return rotulo
    return None


def _bens_individuais(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normaliza bens individuais do detalhe (tipo, descrição, valor).

    Campos adicionais do TSE (``codigoTipoBem``/``ordemBem``) são preservados
    **apenas quando presentes** no payload — sem eles, o item mantém o formato
    enxuto clássico (compatível com consumidores existentes).
    """
    bens = c.get("bens") or []
    if isinstance(bens, dict):
        bens = bens.get("bens") or []
    resultado = []
    for bem in bens:
        if not isinstance(bem, dict):
            continue
        try:
            valor = round(float(bem.get("valor") or 0), 2)
        except (TypeError, ValueError):
            valor = 0.0
        item = {
            "tipo": _texto_or_none(
                bem.get("descricaoDeTipoDeBem")
                or bem.get("tipoBem")
                or bem.get("descricaoTipoBem")
            ),
            "descricao": str(
                bem.get("descricao")
                or bem.get("descricaoDetalhadaBem")
                or bem.get("descricaoBem")
                or ""
            ).strip() or None,
            "valor": valor,
        }
        if bem.get("codigoTipoBem") is not None:
            item["codigo_tipo_bem"] = bem.get("codigoTipoBem")
        ordem = bem.get("ordemBem")
        if ordem is None:
            ordem = bem.get("ordem")
        if ordem is not None:
            item["ordem"] = ordem
        resultado.append(item)
    return resultado


def _resumo_patrimonio(bens: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deriva agregações do patrimônio declarado (sem novas chamadas à API).

    Retorna quantidade de bens, o de maior valor, o de menor valor (ignorando
    itens zerados) e a distribuição somada por tipo de bem, em ordem decrescente.
    """
    quantidade = len(bens)
    maior = None
    menor = None
    for bem in bens:
        valor = float(bem.get("valor") or 0)
        if maior is None or valor > float(maior.get("valor") or 0):
            maior = bem
        if valor > 0 and (menor is None or valor < float(menor.get("valor") or 0)):
            menor = bem

    por_tipo: Dict[str, Dict[str, Any]] = {}
    for bem in bens:
        tipo = _texto_or_none(bem.get("tipo")) or "Não informado"
        item = por_tipo.setdefault(tipo, {"tipo": tipo, "quantidade": 0, "soma": 0.0})
        item["quantidade"] += 1
        item["soma"] = round(item["soma"] + float(bem.get("valor") or 0), 2)

    return {
        "quantidade": quantidade,
        "maiorBem": maior,
        "menorBem": menor,
        "distribuicaoPorTipo": sorted(
            por_tipo.values(), key=lambda x: x["soma"], reverse=True
        ),
    }


def _norm_propostas(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normaliza propostas/plano de governo em entradas estruturadas.

    Aceita tanto listas de strings quanto de dicionários com chaves variadas
    do TSE (``proposta``, ``titulo``, ``tema``, ``descricao``, ``resumo``...).
    As chaves originais são preservadas; ``titulo``/``descricao`` são sempre
    derivados para consumo uniforme. Entradas vazias são descartadas.
    """
    propostas = (
        c.get("propostas")
        or c.get("propostaGoverno")
        or c.get("planoDeGoverno")
        or []
    )
    if isinstance(propostas, dict):
        propostas = (
            propostas.get("propostas") or propostas.get("planoDeGoverno") or []
        )
    resultado = []
    for p in propostas:
        if isinstance(p, dict):
            entrada = dict(p)
            titulo = _texto_or_none(
                entrada.get("titulo") or entrada.get("proposta") or entrada.get("tema")
            )
            descricao = _texto_or_none(
                entrada.get("descricao")
                or entrada.get("texto")
                or entrada.get("resumo")
                or entrada.get("detalhe")
            )
            entrada["titulo"] = titulo
            entrada["descricao"] = descricao
            if not (titulo or descricao):
                continue
        else:
            if not isinstance(p, str):
                continue
            texto = p.strip()
            if not texto:
                continue
            entrada = {"titulo": None, "descricao": texto}
        resultado.append(entrada)
    return resultado


def _norm_redes(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normaliza redes sociais do detalhe, derivando a plataforma por heurística."""
    redes = c.get("redesSociais") or c.get("redes") or []
    if isinstance(redes, dict):
        redes = redes.get("redesSociais") or redes.get("redes") or []
    resultado = []
    for r in redes:
        if not isinstance(r, dict):
            continue
        url = _texto_or_none(
            r.get("url") or r.get("urlRedeSocial") or r.get("endereco")
        )
        resultado.append(
            {
                "titulo": _texto_or_none(
                    r.get("titulo") or r.get("redeSocial") or r.get("nome")
                ),
                "url": url,
                "plataforma": _plataforma_rede(url),
            }
        )
    return resultado


def _norm_detalhe_rico(c: Dict[str, Any], campos: Optional[set]) -> Dict[str, Any]:
    """Monta o payload rico do candidato (o máximo que a página exibe).

    ``campos`` restringe os blocos persistidos; ``None`` = todos.
    Nenhuma chamada extra é feita aqui: tudo vem do mesmo endpoint de detalhe.
    O patrimônio ganha agregados derivados (maior/menor bem e distribuição por
    tipo) e propostas/redes são normalizadas para consumo uniforme.
    """
    partido = c.get("partido") or {}
    coligacao = c.get("coligacao")
    federacao = c.get("federacao")
    rico: Dict[str, Any] = {}

    if campos is None or "dados" in campos:
        rico["dados"] = {
            "nomeCompleto": _texto_or_none(c.get("nomeCompleto")),
            "nomeUrna": _texto_or_none(c.get("nomeUrna")),
            "nomeSocial": _texto_or_none(c.get("nomeSocial")),
            "cidadeNatal": _texto_or_none(
                c.get("cidadeNatal") or c.get("cidadeNascimento")
            ),
            "ufNascimento": _texto_or_none(
                c.get("ufNascimento") or c.get("ufNascimentoCandidato")
            ),
            "cpf": _texto_or_none(c.get("cpf")),
            "email": _texto_or_none(c.get("email")),
            "ocupacao": _texto_or_none(c.get("ocupacao")),
            "grauInstrucao": _texto_or_none(c.get("grauInstrucao")),
            "genero": _texto_or_none(c.get("genero") or c.get("descricaoSexo")),
            "corRaca": _texto_or_none(
                c.get("corRaca") or c.get("descricaoCorRaca") or c.get("raca")
            ),
            "dataNascimento": _texto_or_none(
                c.get("dataNascimento") or c.get("dataDeNascimento")
            ),
            "estadoCivil": _texto_or_none(
                c.get("estadoCivil") or c.get("descricaoEstadoCivil")
            ),
            "situacao": _texto_or_none(
                c.get("descricaoSituacao") or c.get("situacao")
            ),
            "fotoUrl": _texto_or_none(c.get("fotoUrl")),
        }

    if campos is None or "bens" in campos:
        bens = _bens_individuais(c)
        rico["patrimonio"] = {
            "totalDeBens": _total_bens(c),
            "resumo": _resumo_patrimonio(bens),
            "bens": bens,
        }

    if campos is None or "propostas" in campos:
        rico["propostas"] = _norm_propostas(c)

    if campos is None or "redes" in campos:
        rico["redesSociais"] = _norm_redes(c)

    rico["eleicao"] = {
        "partido": _texto_or_none(partido.get("sigla")),
        "numero": c.get("numero"),
        "coligacao": _texto_or_none(coligacao) if isinstance(coligacao, dict) else _texto_or_none(coligacao),
        "federacao": _texto_or_none(federacao) if isinstance(federacao, dict) else _texto_or_none(federacao),
    }
    return rico


def _total_bens(c: Dict[str, Any]) -> float:
    """Soma os bens declarados; usa totalDeBens quando presente."""
    total = c.get("totalDeBens")
    if total is not None:
        try:
            return round(float(total), 2)
        except (TypeError, ValueError):
            pass
    bens = c.get("bens") or []
    if isinstance(bens, dict):
        bens = bens.get("bens") or []
    soma = 0.0
    for bem in bens:
        if isinstance(bem, dict):
            soma += _numero_float(bem.get("valor"))
    return round(soma, 2)


async def _enriquecer_candidato(
    client: httpx.AsyncClient,
    ano: int,
    unidade: str,
    id_eleicao: str,
    candidato_bruto: Dict[str, Any],
    cargo_nome: str,
    campos: Optional[set] = None,
) -> Dict[str, Any]:
    """Busca o detalhe do candidato e mescla com a listagem de forma defensiva.

    ``campos=None`` coleta o máximo (perfil + bens + propostas + redes sociais,
    persistidos em ``tse_candidato_detalhe``). Especificou os blocos? Restringe.
    """
    base = _norm_listagem(candidato_bruto, cargo_nome)
    base["ano"] = ano
    base["total_bens_declarados"] = None
    base["tem_detalhe"] = False

    id_candidato = base.get("id_candidato")
    if id_candidato is None:
        return base

    url = f"{BASE_TSE}/candidatura/buscar/{ano}/{unidade}/{id_eleicao}/candidato/{id_candidato}"
    try:
        dados = await _get_json(client, url)
    except ExtrairTSEError:
        # Falha pontual no detalhe: mantém o que a listagem já trouxe.
        return base

    conteudo = (
        dados.get("candidato")
        or dados.get("dadosCandidato")
        or dados
    )
    if isinstance(conteudo, dict) and "candidato" in conteudo:
        conteudo = conteudo["candidato"]

    detalhe = _norm_detalhe(conteudo)
    base.update(
        {
            **detalhe,
            "ocupacao": base.get("ocupacao") or detalhe.get("ocupacao"),
            "genero": base.get("genero") or detalhe.get("genero"),
            "cor_raca": base.get("cor_raca") or (
                _texto_or_none(
                    conteudo.get("corRaca")
                    or conteudo.get("descricaoCorRaca")
                    or conteudo.get("raca")
                )
            ),
            "situacao": base.get("situacao")
            or detalhe.get("situacao_detalhe")
            or _texto_or_none(
                conteudo.get("descricaoSituacao") or conteudo.get("situacao")
            ),
            "data_nascimento": base.get("data_nascimento")
            or _texto_or_none(
                conteudo.get("dataNascimento") or conteudo.get("dataDeNascimento")
            ),
            "total_bens_declarados": _total_bens(conteudo),
            "tem_detalhe": True,
            "_detalhe_rico": _norm_detalhe_rico(conteudo, campos),
        }
    )
    return base


# ---------------------------------------------------------------------------
# Extração completa
# ---------------------------------------------------------------------------

async def extrair_candidatos(
    ano: int,
    uf: str,
    codigo_cargo: int,
    municipio: Optional[str] = None,
    limite: Optional[int] = None,
    campos: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Busca a lista completa de candidatos + detalhe aprofundado de cada um.

    Retorna uma lista de dicts já normalizados, pronta para vira um DataFrame.
    ``campos`` (ex.: "bens,propostas") restringe os blocos do detalhe rico;
    ``None`` coleta o máximo por candidato.
    """
    campos_norm = _normalizar_campos_detalhe(campos)
    id_eleicao = _id_eleicao(ano)
    municipais = _eh_municipal(ano)
    uf = _validar_uf(uf)
    _validar_cargo(codigo_cargo, municipais=municipais)
    cargo_nome = _nome_cargo(codigo_cargo)

    if municipais and not municipio:
        raise ExtrairTSEError(
            "Nas eleições municipais (2020/2024) informe o código do município."
        )
    unidade = municipio if municipais else uf

    url = f"{BASE_TSE}/candidatura/listar/{ano}/{unidade}/{id_eleicao}/{codigo_cargo}/candidatos"

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        dados = await _get_json(client, url)
        brutos = dados.get("candidatos") or []
        if limite is not None and limite > 0:
            brutos = brutos[: limite]
        if not brutos:
            return []

        # Enriquecimento em paralelo com um pequeno semáforo para não
        # sobrecarregar o rate-limit do TSE (concorrência HTTP on-demand).
        semaforo = asyncio.Semaphore(MAX_CONCORRENCIA)

        async def _com_semaforo(b: Dict[str, Any]) -> Dict[str, Any]:
            async with semaforo:
                return await _enriquecer_candidato(
                    client, ano, unidade, id_eleicao, b, cargo_nome,
                    campos=campos_norm,
                )

        return list(await asyncio.gather(*[_com_semaforo(b) for b in brutos]))


# ---------------------------------------------------------------------------
# Cache local (SQLite) — leitura/salvamento obrigatório antes da API
# ---------------------------------------------------------------------------

def chave_cache(
    ano: int,
    unidade: str,
    codigo_cargo: int,
    limite: Optional[int] = None,
) -> str:
    """Chave canônica de cache por filtro (ano, unidade, cargo, limite)."""
    parte_limite = "completo" if limite is None else f"limite-{int(limite)}"
    return f"tse|{ano}|{unidade.strip().upper()}|{codigo_cargo}|{parte_limite}"


async def obter_candidatos_cacheados(
    ano: int,
    uf: str,
    codigo_cargo: int,
    municipio: Optional[str] = None,
    limite: Optional[int] = None,
    forcar_atualizacao: bool = False,
    ttl_segundos: Optional[int] = None,
    campos: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], str, str]:
    """Ponto único de entrada da extração com cache obrigatório.

    Fluxo:
        1. Valida os filtros (mesmas regras da extração direta).
        2. Se houver base local dentro do TTL (e não forçou atualização), monta
           o resultado direto do banco → resposta instantânea, zero chamadas à
           API do TSE e nenhum risco de 403 por excesso de requisições.
        3. Caso contrário, executa a varredura na API oficial, salva o bruto no
           banco local (incluindo o detalhe rico por candidato) e devolve os dados.

    ``campos`` restringe os blocos do detalhe rico (None = máximo).
    Retorna a tuple (candidatos, origem, cache_key), em que origem é
    "cache" (servido do banco) ou "api" (recém-extraído e persistido).
    """
    campos_norm = _normalizar_campos_detalhe(campos)
    _id_eleicao(ano)  # valida o ano suportado antes de prosseguir
    municipais = _eh_municipal(ano)
    uf = _validar_uf(uf)
    _validar_cargo(codigo_cargo, municipais=municipais)
    if municipais and not municipio:
        raise ExtrairTSEError(
            "Nas eleições municipais (2020/2024) informe o código do município."
        )
    unidade = municipio if municipais else uf
    cache_key = chave_cache(ano, unidade, codigo_cargo, limite)
    ttl = ttl_segundos if ttl_segundos is not None else TSE_CACHE_TTL

    if not forcar_atualizacao and await asyncio.to_thread(
        database.cache_fresco, cache_key, ttl
    ):
        dados = await asyncio.to_thread(database.carregar_candidatos_tse, cache_key)
        if dados is not None:
            await asyncio.to_thread(database.registrar_uso, cache_key)
            return dados, "cache", cache_key

    candidatos = await extrair_candidatos(
        ano, uf, codigo_cargo, municipio=municipio, limite=limite,
        campos=campos_norm if campos_norm else None,
    )
    try:
        await asyncio.to_thread(
            database.salvar_candidatos_tse,
            cache_key, ano, uf, codigo_cargo, municipio, limite,
            candidatos, origem="api",
        )
    except Exception:
        # Falha de persistência (filesystem somente-leitura em deploy) nunca
        # deve impedir a entrega dos resultados.
        pass
    try:
        await asyncio.to_thread(database.salvar_detalhe_candidatos, cache_key, candidatos)
    except Exception:
        pass
    limpos = [
        {k: v for k, v in c.items() if k != "_detalhe_rico"}
        for c in candidatos
    ]
    return limpos, "api", cache_key


# ---------------------------------------------------------------------------
# Execução em segundo plano (BackgroundTasks) — trigger + acompanhamento
# ---------------------------------------------------------------------------
#
# Autorizado pelo AGENTS.md EXCLUSIVAMENTE neste fluxo: a tarefa só é disparada
# por requisição HTTP explícita do operador (rota de extração). Nunca por cron,
# startup/lifespan ou agendamento. O worker registra etapas no banco local para
# consulta via /tse/execucoes/{task_id}.

async def processar_extracao_tse_em_segundo_plano(
    task_id: str,
    ano: int,
    uf: str,
    codigo_cargo: int,
    municipio: Optional[str] = None,
    limite: Optional[int] = None,
    forcar_atualizacao: bool = False,
    campos: Optional[str] = None,
) -> None:
    """Executa a extração pesada fora da requisição HTTP e registra o status.

    Robustez: qualquer falha (rede, timeout, 403 do TSE) é capturada e gravada
    no log da execução — o servidor nunca derruba por causa do worker. Falhas
    também disparam logger.error no terminal do uvicorn (observabilidade).
    """
    try:
        await asyncio.to_thread(
            database.atualizar_execucao_tse, task_id,
            status="Executando", etapa="Coletando dados da API",
        )
        candidatos, origem, cache_key = await obter_candidatos_cacheados(
            ano, uf, codigo_cargo, municipio=municipio, limite=limite,
            forcar_atualizacao=forcar_atualizacao, campos=campos,
        )

        await asyncio.to_thread(
            database.atualizar_execucao_tse, task_id,
            etapa="Aplicando Modelo Base", total_candidatos=len(candidatos),
            origem=origem, cache_key=cache_key,
        )
        df = estrutura_dataframe(candidatos, ano, uf)

        await asyncio.to_thread(
            database.atualizar_execucao_tse, task_id,
            etapa="Salvando cache e gravando .xlsx",
        )
        caminho = gravar_arquivo(df, nome_arquivo(ano, uf, codigo_cargo))

        await asyncio.to_thread(
            database.registrar_evento, "modelo_base",
            f"Extração TSE {ano}/{uf} (cargo {codigo_cargo}): {len(df)} candidato(s) "
            f"gravados no Modelo Base — '{caminho}'.",
        )

        await asyncio.to_thread(
            database.atualizar_execucao_tse, task_id,
            status="Concluído",
            etapa=("Concluído" + (f" — arquivo: {caminho}" if caminho else "")),
            total_candidatos=len(df),
            caminho_arquivo=caminho,
            detalhe=f"Origem: {origem}. {len(df)} candidato(s).",
        )
        logger.info(
            "Extração TSE concluída (task={} ano={} uf={} cargo={}): {} candidato(s), origem={}",
            task_id, ano, uf, codigo_cargo, len(df), origem,
        )
    except ExtrairTSEError as exc:
        msg = _redigir_mensagem(exc)
        logger.error(
            "Extração TSE FALHOU (task={}ano={} uf={} cargo={}): {}",
            task_id, ano, uf, codigo_cargo, msg,
        )
        await asyncio.to_thread(
            database.atualizar_execucao_tse, task_id,
            status="Falhou", etapa="Falhou", detalhe=msg,
        )
        await asyncio.to_thread(
            database.registrar_evento, "extracao_falhou",
            f"TSE {ano}/{uf} (cargo {codigo_cargo}): {msg}",
        )
    except Exception as exc:  # nunca deixe o worker quebrar o processo
        msg = _redigir_mensagem(exc)
        logger.error(
            "Extração TSE FALHOU (task={} ano={} uf={} cargo={}): {}: {}",
            task_id, ano, uf, codigo_cargo, type(exc).__name__, msg,
        )
        await asyncio.to_thread(
            database.atualizar_execucao_tse, task_id,
            status="Falhou", etapa="Falhou",
            detalhe=f"{type(exc).__name__}: {msg}",
        )
        await asyncio.to_thread(
            database.registrar_evento, "extracao_falhou",
            f"TSE {ano}/{uf} (cargo {codigo_cargo}): {type(exc).__name__}: {msg}",
        )


# ---------------------------------------------------------------------------
# Formatação (DataFrame) e Exportação (.xlsx)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Modelo Base Dinâmico — output de inteligência política
# ---------------------------------------------------------------------------
#
# As 25 colunas do MODELO BASE (arquivo de referência em RelMeg - Entregas/TSE/)
# são carregadas em tempo de execução via modelo_base.gabarito_modelo();
# o DataFrame de saída replica a ordem exata e a formatação do gabarito.
# Campos que o TSE não publica (contato, frentes etc.) entram com marcador
# "Não"/"Neutro"/"-" e podem ser preenchidos por base de inteligência própria.

_EIXOS_ATIVIDADE = [
    ("tecnologia", ["engenheiro de software", "tecnologia", "informática", "informatica",
                    "programador", "analista de sistemas", "ciência de dados", "cientista de dados"]),
    ("educação", ["professor", "professora", "educador", "educadora", "pedagogo", "escola",
                  "docente", "ensino", "estudante", "reitor", "coordenador pedagógico"]),
    ("saúde", ["médico", "medica", "enfermeiro", "enfermeira", "saúde", "saude",
               "fisioterapeuta", "psicólogo", "psicologo", "dentista", "farmacêutico",
               "farmaceutico", "hospital", "nutricionista", "biomédico", "biomedico"]),
    ("direito e justiça", ["advogado", "advogada", "jurídico", "juridico", "delegado",
                           "procurador", "magistrado", "juiz", "juíza", "defensor",
                           "promotor", "notário", "notaria", "escrivão"]),
    ("infraestrutura", ["engenheiro", "engenheira", "arquiteto", "construção", "construcao",
                        "obra", "infraestrutura", "eletricista", "encanador", "urbanismo"]),
    ("segurança pública", ["policial", "bombeiro", "segurança", "seguranca", "militar",
                           "forças armadas", "forcas armadas", "agente penitenciário"]),
    ("agronegócio", ["agrônomo", "agronomo", "agropecuário", "agropecuario", "agricultor",
                     "fazendeiro", "pecuarista", "zootecnista", "ruralista", "agronegócio",
                     "agronegocio", "produtor rural"]),
    ("economia e comércio", ["empresário", "empresaria", "administrador", "contador",
                             "economista", "comerciante", "corretor", "financeiro",
                             "contabilidade", "empreendedor", "banqueiro", "industrial"]),
    ("trabalho e servidor público", ["sindicalista", "metalúrgico", "metalurgico",
                                     "operário", "operario", "trabalhador", "bancário",
                                     "bancario", "servidor público", "servidor publico",
                                     "funcionário público", "funcionario publico",
                                     "funcionário", "funcionario", "assessor parlamentar",
                                     "secretário", "secretaria", "agente público"]),
    ("social e assistência", ["assistente social", "serviço social", "servico social",
                              "terapeuta", "filantropia", "voluntário", "voluntaria", "ong"]),
    ("comunicação e cultura", ["comunicador", "jornalista", "radialista", "publicitário",
                               "publicitario", "locutor", "apresentador", "artista", "músico",
                               "musico", "cultura", "relações públicas", "relacoes publicas",
                               "religioso", "pastor", "pastora", "missionário", "missionaria"]),
    ("transporte", ["motorista", "transportador", "taxista", "caminhoneiro", "logística",
                    "logistica", "piloto", "navegador", "ferroviário"]),
    ("esporte e lazer", ["esportista", "atleta", "personal", "atividade física",
                         "atividade fisica", "esporte"]),
]


def _slug(texto: str) -> str:
    """Normaliza minúsculas e remove acentos (comparação de termos)."""
    texto = str(texto or "").lower()
    return _ud.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii")


def _valor_data(valor: Any):
    """Converte a data de nascimento para date (ISO ou dd/mm/aaaa)."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return _dt.datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    return None


def _idade(nascimento) -> Optional[int]:
    """Idade em anos completos na data atual."""
    if not nascimento:
        return None
    hoje = _dt.date.today()
    return hoje.year - nascimento.year - (
        (hoje.month, hoje.day) < (nascimento.month, nascimento.day)
    )


def _casa_legislativa(c: Dict[str, Any]) -> str:
    """Casa/instituição a partir do cargo declarado no TSE."""
    cargo = _slug(c.get("cargo") or "")
    if "senador" in cargo:
        return "Senado Federal"
    if "deputado federal" in cargo:
        return "Câmara dos Deputados"
    if "estadual" in cargo or "distrital" in cargo:
        return "Assembleia Legislativa"
    if "prefeito" in cargo or "vice-prefeito" in cargo:
        return "Prefeitura"
    if "vereador" in cargo:
        return "Câmara Municipal"
    if "presidente" in cargo or "governador" in cargo or "vice-governador" in cargo:
        return "Poder Executivo"
    return "Câmara dos Deputados"


def _eixo_atividade(ocupacao: Any) -> str:
    """Classifica o eixo temático com base na ocupação declarada."""
    texto = _slug(ocupacao)
    if not texto:
        return "Outros"
    for eixo, palavras in _EIXOS_ATIVIDADE:
        if any(_slug(palavra) in texto for palavra in palavras):
            return eixo[0].upper() + eixo[1:]
    return "Outros"


def _observacao(c: Dict[str, Any]) -> str:
    """Observação consolidada: patrimônio, situação do registro e teto de gastos."""
    partes = []
    bens = c.get("total_bens_declarados")
    if bens not in (None, "", 0):
        try:
            valor = float(bens)
            if valor:
                partes.append(f"Bens declarados: R$ {valor:,.2f}".replace(",", "X")
                              .replace(".", ",").replace("X", "."))
        except (TypeError, ValueError):
            pass
    situacao = str(c.get("situacao") or "").strip()
    if situacao:
        partes.append(f"Situação do registro: {situacao}")
    partes.append("Teto de gastos: consultar o portal da Justiça Eleitoral/prestação de contas.")
    return " | ".join(partes)


def _linha_modelo(c: Dict[str, Any], ano: int, uf: str) -> List[Any]:
    """Monta uma linha nas 25 posições exatas do MODELO BASE."""
    nascimento = _valor_data(c.get("data_nascimento"))
    ocupacao = str(c.get("ocupacao") or "—").strip()
    nome = str(c.get("nome_urna") or c.get("nome_completo") or "").strip().upper()

    return [
        _casa_legislativa(c),                       # Casa Legislativa
        nome or "—",                                 # Nome
        str(c.get("partido_sigla") or "").strip().upper(),  # Partido
        str(uf or "").upper(),                       # UF
        "Eleição",                                   # Eleição/ Reeleição
        "Não", "Não", "Não", "Não",                  # FPE, FCS, FPBio, FPEvang
        "Neutro",                                    # Sinergia FPE
        _eixo_atividade(c.get("ocupacao")),          # Eixo de Atuação
        "-", "-", "-", "-", "-", "-",                # Celular, Assessoria, Contato, E-mail, Gabinete, Rede
        ocupacao,                                    # Profissão
        nascimento,                                  # Data de Nascimento
        _idade(nascimento),                          # Idade
        str(c.get("cor_raca") or "").strip(),        # Cor/Raça
        str(c.get("genero") or "").strip(),          # Gênero
        str(c.get("grau_instrucao") or "").strip(),  # Escolaridade
        str(c.get("estado_civil") or "").strip(),    # Estado Civil
        _observacao(c),                              # Observação
    ]


def estrutura_dataframe(
    candidatos: List[Dict[str, Any]], ano: int, uf: str
) -> pd.DataFrame:
    """Monta o DataFrame final seguindo fielmente o layout do MODELO BASE."""
    colunas = gabarito_modelo()["colunas"]
    if not candidatos:
        return pd.DataFrame(columns=colunas)

    df = pd.DataFrame(_linha_modelo(c, ano, uf) for c in candidatos)
    df.columns = colunas

    # Ordenação estável para leitura em ferramentas de BI.
    df = df.sort_values(
        ["Partido", "Nome"],
        key=lambda s: s.fillna("").astype(str).str.lower(),
        na_position="last",
    ).reset_index(drop=True)
    return df


def _bytes_xlsx(df: pd.DataFrame) -> bytes:
    """Serializa o DataFrame final (.xlsx em memória) seguindo o MODELO BASE."""
    return bytes_com_gabarito(df)


def nome_arquivo(ano: int, uf: str, codigo_cargo: int) -> str:
    """Nome dinâmico do arquivo: TSE_{ano}_{UF}_{Cargo}.xlsx"""
    return f"TSE_{ano}_{uf.strip().upper()}_{_nome_cargo(codigo_cargo)}.xlsx"


def caminho_entrega(nome: str) -> Optional[Path]:
    """Caminho completo na pasta de entregas TSE (config.py) ou None."""
    try:
        pasta = settings.dir_tse
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta / nome
    except OSError:
        # Ambientes efêmeros (Render/Vercel) não permitem escrita: desativa o disco.
        return None


def gravar_arquivo(df: pd.DataFrame, nome: str) -> Optional[str]:
    """Grava o .xlsx no diretório de entregas seguindo o MODELO BASE."""
    destino = caminho_entrega(nome)
    if destino is None:
        return None
    gravar_com_gabarito(df, destino)
    return str(destino)


# ---------------------------------------------------------------------------
# Rota FastAPI
# ---------------------------------------------------------------------------

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import Request

from rate_limit import limiter, LIMITE_TSE

router = APIRouter(prefix="/tse", tags=["TSE / Exportação Excel"])


@router.get("/exportar/{ano}/{uf}/{codigo_cargo}")
@limiter.limit(LIMITE_TSE)
async def exportar_candidatos_excel(
    request: Request,
    background_tasks: BackgroundTasks,
    ano: int = Path(..., ge=2018, le=2100, description="Ano eleitoral (2020, 2022, 2024, 2026...)"),
    uf: str = Path(..., min_length=2, max_length=2, description="Sigla da UF (ex: GO)"),
    codigo_cargo: int = Path(..., ge=1, le=13, description="Código do cargo (7 = Deputado Estadual)"),
    municipio: Optional[str] = Query(None, max_length=12, description="Código do município (obrigatório em eleições municipais)"),
    limite: Optional[int] = Query(None, ge=1, le=2000, description="Limite opcional de candidatos a enriquecer"),
    forcar_atualizacao: bool = Query(False, description="True = ignora o cache e reextrai da API oficial do TSE"),
    em_segundo_plano: bool = Query(True, description="True = trigger assíncrono (202 Accepted + /tse/execucoes/{id}). False = executa síncrono e retorna o resumo."),
    sincrono: bool = Query(False, description="Compatibilidade: True força o antigo comportamento síncrono (resumo JSON)."),
    download: bool = Query(False, description="True = baixa o .xlsx como arquivo (força execução síncrona)"),
    campos: Optional[str] = Query(
        None,
        description="Blocos do detalhe por candidato (ex.: 'bens,propostas'). Vazio = coleta o MÁXIMO (dados, bens, propostas, redes sociais).",
    ),
):
    """
    Extrai/enriquece candidatos do TSE e exporta para 'RelMeg - Entregas/TSE'.

    CONFORME AGENTS.md — o disparo é SEMPRE uma ação explícita do operador:
    esta rota é o trigger. Por padrão responde **202 Accepted** e executa a
    extração pesada como BackgroundTask, devolvendo o `task_id` para
    acompanhamento em `GET /tse/execucoes/{task_id}` (imune a timeouts do
    cliente e a 403/indisponibilidade do TSE quando o cache local tiver dados).

    Regra de volume: sem `campos` o motor coleta O MÁXIMO (perfil, bens
    individuais, propostas e redes sociais de cada candidato, persistidos em
    `tse_candidato_detalhe` e legíveis em GET /tse/detalhe/{cache_key}/{id}).
    Com `campos` (ex.: `campos=bens`) a coleta fica restrita aos blocos pedidos.

    Parâmetros de compatibilidade:
    - `em_segundo_plano=false` ou `sincrono=true`: comportamento antigo,
      resposta imediata com o resumo JSON.
    - `download=true`: retorna o .xlsx (força modo síncrono — o stream precisa
      do resultado na resposta).
    """
    try:
        campos_norm = _normalizar_campos_detalhe(campos)
    except ExtrairTSEError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if sincrono or download or not em_segundo_plano:
        return await _executar_extracao_sincrona(
            ano, uf, codigo_cargo,
            municipio=municipio, limite=limite,
            forcar_atualizacao=forcar_atualizacao, download=download,
            campos=campos_norm if campos_norm else None,
        )

# Trigger assíncrono: valida os parâmetros ANTES de aceitar a tarefa.
    try:
        _id_eleicao(ano)
        _validar_uf(uf)
        _validar_cargo(codigo_cargo, municipais=_eh_municipal(ano))
        if _eh_municipal(ano) and not municipio:
            raise ExtrairTSEError(
                "Nas eleições municipais (2020/2024) informe o código do município."
            )
    except ExtrairTSEError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # AGENTS.md: recusa disparo concorrente para o mesmo escopo (409).
    # A criação é atômica (check + insert sob BEGIN IMMEDIATE em database.py):
    # dois disparos simultâneos não conseguem passar juntos (sem janela TOCTOU).
    task_id = str(uuid.uuid4())
    ativa = await asyncio.to_thread(
        database.iniciar_execucao_tse,
        task_id, ano, uf, codigo_cargo,
        municipio=municipio, limite=limite, forcar_atualizacao=forcar_atualizacao,
    )
    if ativa:
        raise HTTPException(
            status_code=409,
            detail={
                "mensagem": "Já existe uma extração em andamento para estes filtros. "
                            "Acompanhe por GET /tse/execucoes/{task_id}.",
                "execucao": ativa,
            },
        )

    background_tasks.add_task(
        processar_extracao_tse_em_segundo_plano,
        task_id, ano, uf, codigo_cargo,
        municipio=municipio, limite=limite, forcar_atualizacao=forcar_atualizacao,
        campos=campos_norm if campos_norm else None,
    )
    return JSONResponse(
        content={
            "status": "Processamento iniciado",
            "task_id": task_id,
            "url_status": f"/tse/execucoes/{task_id}",
            "parametros": {
                "ano": ano, "uf": uf, "codigo_cargo": codigo_cargo,
                "municipio": municipio, "limite": limite,
                "forcar_atualizacao": forcar_atualizacao,
                "campos": sorted(campos_norm) if campos_norm else "todos",
            },
        },
        status_code=202,
    )


async def _executar_extracao_sincrona(
    ano: int,
    uf: str,
    codigo_cargo: int,
    municipio: Optional[str] = None,
    limite: Optional[int] = None,
    forcar_atualizacao: bool = False,
    download: bool = False,
    campos: Optional[str] = None,
):
    """Fluxo síncrono legado (resumo JSON ou .xlsx), sempre passando pelo cache."""
    # Validação de input do operador ANTES de tocar a API (400, não 502).
    try:
        _id_eleicao(ano)
        _validar_uf(uf)
        _validar_cargo(codigo_cargo, municipais=_eh_municipal(ano))
        if _eh_municipal(ano) and not municipio:
            raise ExtrairTSEError(
                "Nas eleições municipais (2020/2024) informe o código do município."
            )
    except ExtrairTSEError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        candidatos, origem, cache_key = await obter_candidatos_cacheados(
            ano, uf, codigo_cargo,
            municipio=municipio, limite=limite, forcar_atualizacao=forcar_atualizacao,
            campos=campos,
        )
    except ExtrairTSEError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    df = estrutura_dataframe(candidatos, ano, uf)
    nome = nome_arquivo(ano, uf, codigo_cargo)
    gravar_arquivo(df, nome)

    if download:
        conteudo = _bytes_xlsx(df)
        return StreamingResponse(
            io.BytesIO(conteudo),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": f'attachment; filename="{nome}"'},
        )

    return {
        "arquivo": nome,
        "total_candidatos": len(df),
        "origem": origem,
        "cache_key": cache_key,
        "colunas": list(df.columns),
        "amostra": df.head(5).to_dict(orient="records"),
        "ponte_looker": "Arquivo .xlsx exportado para o diretório 'RelMeg - Entregas/TSE'. "
        "Carregue no Looker Studio como Upload do Google Drive/Sheets.",
    }


def _somente_nome(caminho: Any) -> Any:
    """A3 — expõe apenas o nome do arquivo no JSON (nunca o caminho absoluto)."""
    from pathlib import Path as _PathLib

    if caminho is None:
        return None
    return _PathLib(str(caminho)).name


@router.get("/execucoes")
@limiter.limit(LIMITE_TSE)
async def listar_execucoes(
    request: Request,
    limite: int = Query(20, ge=1, le=100, description="Quantidade de execuções recentes"),
):
    """Lista as extrações recentes (task_id, filtros, status)."""
    execucoes = await asyncio.to_thread(database.listar_execucoes_tse, limite)
    for execucao in execucoes:
        if "caminho_arquivo" in execucao:
            execucao["caminho_arquivo"] = _somente_nome(execucao["caminho_arquivo"])
    return {"total": limite, "execucoes": execucoes}


@router.get("/execucoes/{task_id}")
@limiter.limit(LIMITE_TSE)
async def status_execucao(
    request: Request,
    task_id: str = Path(..., min_length=8, description="ID da execução retornado pelo trigger"),
):
    """Status e log de etapas de uma extração em segundo plano."""
    registro = await asyncio.to_thread(database.obter_execucao_tse, task_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    registro["pronto"] = registro.get("status") in ("Concluído", "Falhou")
    if registro.get("caminho_arquivo"):
        registro["caminho_arquivo"] = _somente_nome(registro["caminho_arquivo"])
    return registro


@router.get("/detalhe/{cache_key}/{id_candidato}")
@limiter.limit(LIMITE_TSE)
async def detalhe_candidato_cacheados(
    request: Request,
    cache_key: str = Path(..., min_length=8, description="Chave de cache da extração (campo 'cache_key' da resposta/status)"),
    id_candidato: str = Path(..., min_length=1, description="ID do candidato no TSE"),
):
    """Lê o detalhe RICO (máximo) coletado de um candidato — do cache local, sem rede.

    O payload inclui perfil, patrimônio (bens individuais), propostas e redes
    sociais, conforme os blocos pedidos na extração (sem `campos` = tudo).
    """
    detalhe = await asyncio.to_thread(
        database.buscar_detalhe_candidato, cache_key, id_candidato
    )
    if detalhe is None:
        raise HTTPException(
            status_code=404,
            detail="Detalhe rico ainda não coletado para este candidato. ",
        )
    detalhe.pop("capturado_em", None)
    return {
        "cache_key": cache_key,
        "id_candidato": id_candidato,
        "detalhe": detalhe,
    }
