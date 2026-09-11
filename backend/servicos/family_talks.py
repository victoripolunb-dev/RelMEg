"""
Matriz de Inteligência Family Talks — filtro de relevância das proposições.

Perfil institucional: OSCIP de advocacy focada no fortalecimento das famílias,
com autonomia familiar, protagonismo das famílias e o Estado como apoio, dentro
de uma cultura de diálogo multipartidário.

Este módulo é o coração do filtro "inteligente" do Clipping:
    1. Cada ementa é normalizada e cruzada com a MATRIZ DE TEMAS PRIORITÁRIOS;
    2. Regras de EXCLUSÃO AUTOMÁTICA disparam primeiro (pautas fora do escopo:
       direito de família estritamente jurídico, alienação parental, direito
       penal ligado à dinâmica familiar, etc.);
    3. Proposições que não tocam nenhum tema prioritário são descartadas.

Conformidade com AGENTS.md: análise pura e determinística (sem API externa).
A varredura em si continua estritamente sob demanda — o operador dispara a
rota; nada roda em segundo plano ou por agendamento.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional

MISSAO_FAMILY_TALKS = (
    "Fortalecimento das famílias: autonomia familiar, protagonismo das famílias "
    "com o Estado como apoio e cultura do diálogo multipartidário."
)

# Tipos aceitos na varredura (Família/Câmara + Senado).
SIGLAS_CAMARA = ["PEC", "PLP", "PL", "MPV", "PDC"]
SIGLAS_SENADO = ["PEC", "PLS", "PL", "PLP", "PDL", "Emenda", "Substitutivo"]

# ---------------------------------------------------------------------------
# Matriz de temas prioritários (dentro do escopo)
# ---------------------------------------------------------------------------

_TEMAS_PRIORITARIOS: List[Dict[str, Any]] = [
    {
        "tema": "Parentalidade e licenças",
        "palavras": [
            "licença maternidade", "licença paternidade", "licença parental",
            "licença-maternidade", "licença-paternidade", "licença gestante",
            "maternidade", "paternidade", "licença por adoção", "cuidado de filhos",
            "dependentes", "acompanhamento de filhos", "responsabilidade parental",
        ],
    },
    {
        "tema": "Orçamento voltado à pauta familiar",
        "palavras": [
            "orçamento da família", "orçamento família", "benefício de prestação continuada",
            "bpc", "transferência de renda", "bolsa família", "fundo da infância",
            "fundo dos direitos da criança", "fundo da primeira infância", "auxílio",
        ],
    },
    {
        "tema": "ECA e proteção de crianças e adolescentes",
        "palavras": [
            "estatuto da criança", "eca", "criança", "crianças", "adolescente",
            "adolescentes", "menor", "menores", "infância", "primeira infância",
            "infantojuvenil", "infanto-juvenil", "brincar", "brincadeira",
        ],
    },
    {
        "tema": "Vínculos e convivência familiar",
        "palavras": [
            "convivência familiar", "convivência comunitária", "vínculo familiar",
            "reunificação familiar", "apadrinhamento", "família acolhedora",
            "família substituta", "adoção", "acolhimento", "guarda", "tutela",
        ],
    },
    {
        "tema": "Prevenção e enfrentamento da violência familiar",
        "palavras": [
            "violência familiar", "violência doméstica", "violência contra a criança",
            "violência contra crianças", "violência infantil", "maus-tratos",
            "maus tratos", "abuso infantil", "exploração sexual de crianças",
            "trabalho infantil", "violência psicológica", "luto familiar",
            "violência intrafamiliar", "negligência",
        ],
    },
    {
        "tema": "Relação família-escola na educação",
        "palavras": [
            "família e escola", "família-escola", "escola e família", "evasão escolar",
            "frequência escolar", "ambiente escolar", "educação infantil", "creche",
            "pré-escola", "escola em tempo integral", "merenda escolar",
            "transporte escolar", "bullying",
        ],
    },
    {
        "tema": "Idoso e família",
        "palavras": [
            "idoso", "idosos", "pessoa idosa", "terceira idade", "estatuto do idoso",
            "cuidado com idosos", "longevidade", "envelhecimento", "cuidador",
            "cuidadores",
        ],
    },
    {
        "tema": "Assistência social com foco familiar",
        "palavras": [
            "assistência social", "proteção social", "sistema único de assistência social",
            "cras", "creas", "socioassistencial", "vulnerabilidade social",
            "emergência social", "população de rua", "famílias em situação de risco",
            "primeira infância e assistência",
        ],
    },
    {
        "tema": "Saúde mental de crianças e adolescentes",
        "palavras": [
            "saúde mental", "saúde mental infantil", "saúde mental da criança",
            "depressão infantil", "ansiedade infantil", "autismo", "tea",
            "tdah", "suicídio juvenil", "sofrimento psíquico",
        ],
    },
    {
        "tema": "Proteção de menores no mundo digital",
        "palavras": [
            "mundo digital", "protagonismo digital", "cyberbullying", "ciberbullying",
            "assédio on-line", "assédio online", "criança e internet",
            "crianças e internet", "dependência digital", "uso de telas",
            "exposição de crianças", "dados pessoais de crianças", "golpe digital",
            "segurança digital", "conteúdo impróprio", "jogos eletrônicos",
        ],
    },
    {
        "tema": "Impacto da saúde na dinâmica familiar (prevenção)",
        "palavras": [
            "prevenção", "vacinação", "aleitamento materno", "amamentação",
            "gestante", "pré-natal", "puericultura", "saúde da família",
            "saúde da criança", "nutrição infantil", "atendimento familiar",
            "bebê", "recém-nascido",
        ],
    },
]

# ---------------------------------------------------------------------------
# Regras de EXCLUSÃO AUTOMÁTICA (fora do escopo) — disparam primeiro
# ---------------------------------------------------------------------------

_TEMAS_FORA_ESCOPO: List[Dict[str, Any]] = [
    {
        "tema": "Direito de família estritamente jurídico",
        "palavras": [
            "divórcio", "separação judicial", "pensão alimentícia", "partilha de bens",
            "inventário", "união estável", "atribuição de guarda", "guarda judicial",
            "curatela", "interdição",
        ],
    },
    {
        "tema": "Alienação parental",
        "palavras": ["alienação parental", "síndrome da alienação parental"],
    },
    {
        "tema": "Direito penal relacionado à dinâmica familiar",
        "palavras": [
            "tipificação penal", "sanção penal", "código penal", "reforma penal",
            "processo penal", "tribunal do júri", "execução penal", "regime prisional",
            "reativação de penas",
        ],
    },
    {
        "tema": "Marcadores estranhos à pauta familiar",
        "palavras": [
            "reforma tributária", "imposto de renda", "agronegócio", "safra",
            "taxa de juros", "câmbio", "tarifa de energia", "concessão de rodovias",
        ],
    },
]


def _normaliza(texto: Any) -> str:
    """Minúsculas + remoção de acentos para comparação determinística."""
    texto = str(texto or "").lower()
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii")


def _hits(palavras: List[str], texto_normalizado: str) -> List[str]:
    """Termos da matriz presentes no texto (evitando duplicidades)."""
    achados = []
    for termo in palavras:
        if _normaliza(termo) in texto_normalizado and termo not in achados:
            achados.append(termo)
    return achados


def analisar_texto(texto: Any) -> Dict[str, Any]:
    """Cruza a ementa com a matriz Family Talks.

    Retorna dicionário com:
        - relevante:  bool
        - temas:      temas prioritários atingidos
        - termo_principal: primeiro termo que fez o match (depuração)
        - motivo:     razão da recusa, quando descartada
        - fora_escopo: tema de exclusão que disparou (ou None)
    """
    texto_norm = _normaliza(texto)

    # 1) Exclusão automática primeiro (regras rígidas do cliente).
    for bloco in _TEMAS_FORA_ESCOPO:
        achados = _hits(bloco["palavras"], texto_norm)
        if achados:
            return {
                "relevante": False,
                "temas": [],
                "termo_principal": achados[0],
                "motivo": f"fora do escopo: {bloco['tema']} ({achados[0]})",
                "fora_escopo": bloco["tema"],
            }

    # 2) Temas prioritários.
    temas = []
    termo_principal = None
    for tema in _TEMAS_PRIORITARIOS:
        achados = _hits(tema["palavras"], texto_norm)
        if achados:
            temas.append({"tema": tema["tema"], "termos": achados})
            if termo_principal is None:
                termo_principal = achados[0]

    relevante = bool(temas)
    return {
        "relevante": relevante,
        "temas": temas,
        "termo_principal": termo_principal,
        "motivo": None if relevante else "nenhum tema prioritário identificado na ementa",
        "fora_escopo": None,
    }


def filtrar(
    itens: List[Dict[str, Any]],
    *,
    campo_ementa: str = "ementa",
    campo_autor: str = "autor",
    anotar: bool = True,
) -> List[Dict[str, Any]]:
    """Filtra proposições/matérias pela matriz Family Talks (em place).

    Item aprovado recebe/renova as chaves:
        - _ft_temas:  temas prioritários detectados
        - _ft_motivo: None (aprovado) ou razão da recusa
    O autor também entra na análise (senadores/candidatos citam o tema).
    """
    aprovados: List[Dict[str, Any]] = []
    for item in itens or []:
        if not isinstance(item, dict):
            continue
        # Análise sobre a ementa; na ausência dela, sobre o autor (fallback).
        texto = item.get(campo_ementa) or item.get(campo_autor) or ""
        analise = analisar_texto(texto)
        if anotar:
            item["_ft_temas"] = analise["temas"]
            item["_ft_motivo"] = analise["motivo"]
        if analise["relevante"]:
            aprovados.append(item)
    return aprovados


def temas_detectados(itens: List[Dict[str, Any]]) -> Dict[str, int]:
    """Contagem de proposições por tema detectado (para o relatório do Clipping)."""
    contagem: Dict[str, int] = {}
    for item in itens or []:
        for tema in item.get("_ft_temas") or []:
            nome = tema["tema"]
            contagem[nome] = contagem.get(nome, 0) + 1
    return contagem


def keywords_para_busca(limite: int = 16) -> List[str]:
    """Termos de busca derivados da matriz (cota limitada de requisições à API).

    São termos de alto poder discriminatório; o filtro final é sempre
    ``analisar_texto`` aplicado sobre a ementa completa.
    """
    selecionadas = [
        "licença maternidade", "licença paternidade",
        "bolsa família", "primeira infância", "adoção", "família acolhedora",
        "guarda", "violência doméstica", "maus-tratos", "trabalho infantil",
        "evasão escolar", "creche", "idosos", "assistência social",
        "saúde mental", "bullying", "cyberbullying", "criança e internet",
        "aleitamento materno", "vulnerabilidade social",
    ]
    return [s for s in selecionadas if s][:limite]