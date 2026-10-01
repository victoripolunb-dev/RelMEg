# -*- coding: utf-8 -*-
"""
Nova proposição — SETOR DE EDUCAÇÃO — setembro/2026. Coleta sob demanda.

Estratégia (decisão 01/10/2026, após cross-check de cobertura):
    A busca por ``keywords`` da Câmara é PERDIDA — sondagem de 01/10/2026 mostrou
    que ela devolve no máximo os 100 primeiros itens por ordenação de id e, para
    termos genéricos, vários PLs de setembro ficam fora. Em vez de 27 buscas por
    palavra-chave, varremos o mês INTEIRO por sigla (PL/PLP/PEC/PDC/MPV) e
    aplicamos a matriz de relevância do setor LOCALMENTE sobre o ementa. São ~8
    requisições à Câmara em vez de 27, com cobertura completa do período.

    Senado: a API ignora ``palavra`` e ``offset`` — trazemos o ano de cada sigla
    numa chamada e filtramos mês + matriz localmente.

Conformidade (AGENTS.md): script manual, nenhuma rotina de fundo, nenhum
agendamento. Só roda quando o operador executa.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings  # noqa: E402

ANO = 2026
JANELA_INI = date(2026, 9, 1)
JANELA_FIM = date(2026, 9, 30)

SIGLAS_CAMARA = ["PL", "PLP", "PEC", "PDC", "MPV"]
SIGLAS_SENADO = ["PL", "PLP", "PEC", "PLN", "PDL"]

ITENS = 100
MAX_PAGINAS = 40
PAUSA_CAMARA = 0.6
PAUSA_SENADO = 1.5
PAUSA_AUTOR = 0.35

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36",
      "Accept": "application/json"}

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"
URL_FICHA_CAMARA = "https://www.camara.leg.br/proposicoesWeb/fichadetramitacao?idProposicao={id}"
URL_FICHA_SENADO = "https://www25.senado.leg.br/web/atividade/materias/-/materia/{codigo}"

DIR_SAIDA = settings.dir_entregas / settings.subdir_novas_proposicoes
ARQUIVO = DIR_SAIDA / "proposicoes_educacao_setembro_2026.json"

# ---------------------------------------------------------------------------
# Matriz de relevância do SETOR DE EDUCAÇÃO
# ---------------------------------------------------------------------------
# Termos nuclear (marca a proposição como sendo de educação) e termos de
# contexto (só contam acompanhados de nuclear). Disparam primeiro as exclusões:
# proposições de saúde, previdência, segurança etc. que a matriz ampla do
# cross-check trazia por SINÔNIMO IMPRÓCISO.

NUCLEAR = [
    # instituição / política de educação
    "educacao", "educacional", "educativo", "educadores", "educadora",
    "ministerio da educacao", "conselho nacional de educacao",
    "sistema nacional de educacao", "plano nacional de educacao",
    # níveis / etapas
    "ensino", "ensinofundamental", "ensino basico", "educacao basica",
    "ensino medio", "educacao infantil", "educacao fundamental",
    "ensino superior", "ensino tecnico", "educacao profissional",
    "educacao tecnologica", "ensino remoto", "educacao a distancia",
    "ensino a distancia", "educacao distancia",
    # escola / corpo discente / docente
    "escolar", "escolares", "escola", "escolas", "creche", "pre-escola",
    "comunidade escolar", "estudante", "estudantes", "estudantil",
    "professor", "professora", "professores", "docente", "docentes",
    "magisterio", "carreira docente", "turma", "turmas", "matricula",
    "matriculas", "matricular", "ensinar", "aprendizagem", "pedagogia",
    "pedagogico", "didatica", "alfabetizacao", "letramento",
    # ensino superior
    "universidade", "universidades", "universitaria", "universitario",
    "faculdade", "faculdades", "instituto federal", "cefet",
    "centro federal de educacao tecnologica",
    "instituicao de ensino", "instituicoes de ensino",
    "autarquia de ensino", "fundacao de ensino",
    # programas / políticas / órgãos
    "fundeb", "fies", "prouni", "enem", "enamed", "inep", "cotas",
    "assistencia estudantil", "pnae", "merenda escolar", "alimentacao escolar",
    "bolsa de pesquisa", "instituicao de pesquisa",
    "educacao inclusiva", "educacao especial", "inclusao escolar",
    "educacao de jovens e adultos", "educacao quilombola", "educacao do campo",
    "tecnologia assistiva", "acessibilidade digital",
    "politica nacional de educacao digital",
]

CONTEXTO = [
    "medicina", "medico", "saude", "sus", "hospital", "enfermagem",
    "previdencia", "aposentadoria", "pensao", "seguranca publica",
    "policia", "carcere", "tributario", "imposto", "licitacao",
    "energia", "ambiental", "agricultura", "emprego", "empresa",
    "autoescola", "improbidade", "corrupcao", "transparencia",
]

# Termos de contexto que, sozinhos, indicam um projeto NÃO educacional
# mesmo quando accompanied de palavra nuclear fraca (ex.: "auto**escola**s").
EXCISAO = [
    "autoescola", "autoescolas", "improbidade administrativa",
    "violencia contra a mulher", "lei maria da penha",
    "perfilamento", "sistemas de recomendacao",
]


def norm(texto: Any) -> str:
    texto = str(texto or "").lower()
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode("ascii")


def _data(valor: Any) -> Optional[date]:
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Classificação por sub-tema do setor
# ---------------------------------------------------------------------------

SUBTEMAS: List[Tuple[str, List[str]]] = [
    # Ordem por especificidade: o primeiro sub-tema que casar vence, para que
    # ementas que mencionam "educação superior" no meio do texto caiam no tema
    # mais preciso (ex.: Pé-de-Meia Universitário vira financiamento, não superior).
    ("Educação Infantil", ["educacao infantil", "creche", "pre-escola", "preescola",
                           "primeira infancia", "auxilio-creche", "auxilio creche"]),
    ("Educação Profissional e Técnica", ["educacao profissional", "ensino tecnico",
                                         "curso tecnico", "educacao tecnica",
                                         "pronatec", "ensino medio tecnico"]),
    ("EaD — Educação a Distância", ["educacao a distancia", "ensino a distancia",
                                    "educacao distancia", "ensino remoto",
                                    "educacao digital", "tecnologia assistiva"]),
    ("Financiamento e Assistência Estudantil", ["fundeb", "fies", "prouni", "cotas",
                                                "assistencia estudantil", "bolsa",
                                                "pe-de-meia", "vaa", "pnae",
                                                "merenda escolar", "alimentacao escolar",
                                                "auxilio-creche", "reembolso-creche"]),
    ("Avaliação e Ingresso", ["enem", "enamed", "vestibular", "vestibulares",
                              "concurso para ingresso", "selecao para ingresso"]),
    ("Regulação e Gestão do MEC", ["ministerio da educacao", "conselho nacional de educacao",
                                   "sistema nacional de educacao", "mec", "inep",
                                   "plano nacional de educacao", "instituto nacional",
                                   "avaliacao da educacao superior",
                                   "supervisao e avaliacao"]),
    ("Educação Especial e Inclusão", ["educacao especial", "educacao inclusiva",
                                       "inclusao escolar", "deficiencia", "tecnologia assistiva"]),
    ("Formação Médica", ["curso de medicina", "cursos de medicina", "medicina",
                         "residência médica", "residência medica",
                         "faculdade de medicina", "medico-veterinario"]),
    ("Educação Privada", ["ensino privado", "educacao particular", "escola particular",
                          "escolas particulares", "mensalidade", "mensalidades",
                          "instituicao de ensino privado", "sistema s"]),
    ("Ensino Médio", ["ensino medio"]),
    ("Ensino Fundamental", ["ensino fundamental", "ensino basico", "educacao basica",
                            "turmas da educacao basica", "ano letivo"]),
    ("Ensino Superior", ["ensino superior", "universidade", "universidades",
                         "faculdade", "faculdades", "instituicao de ensino superior",
                         "universitario", "universitaria", "instituto federal",
                         "cefet", "educacao tecnologica"]),
    ("Profissionais da Educação", ["professor", "professora", "professores",
                                   "docente", "docentes", "carreira docente",
                                   "magisterio"]),
    ("Tecnologia e Inovação na Educação", ["tecnologia educacional",
                                           "plataformas educacionais",
                                           "inteligencia artificial na educacao",
                                           "conectividade escolar",
                                           "internet nas escolas"]),
    ("Continuidade Educacional em Emergências", ["eventos climaticos extremos",
                                                 "calamidade publica", "emergencia",
                                                 "passaporte educacional"]),
]


def _tem(termos: List[str], texto: str) -> List[str]:
    """Casamento por limite de PALAVRA — evita 'ead' dentro de 'base|ada',
    'escola' dentro de 'auto|escolas' e 'de' dentro de qualquer palavra."""
    achados = []
    for t in termos:
        alvo = norm(t)
        padrao = r"(?<![a-z0-9])" + re.escape(alvo).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        if re.search(padrao, texto):
            achados.append(t)
    return achados


def subtema(ementa: str) -> str:
    texto = norm(ementa)
    for nome, termos in SUBTEMAS:
        if _tem(termos, texto):
            return nome
    return "Outros temas de educação"


def relevante(ementa: str) -> Tuple[bool, str]:
    """(bool, motivo). Exclusões disparam antes do termo nuclear."""
    texto = norm(ementa)
    if _tem(EXCISAO, texto):
        return False, "excluído: domínio não educacional"
    nuclear = _tem(NUCLEAR, texto)
    if not nuclear:
        return False, "sem termo nuclear do setor educação"
    contexto = _tem(CONTEXTO, texto)
    return True, f"nucleares: {', '.join(nuclear[:6])}" + (
        f" | contexto: {', '.join(contexto[:4])}" if contexto else "")
    return True, f"nucleares: {', '.join(nuclear[:6])}" + (
        f" | contexto: {', '.join(contexto[:4])}" if contexto else "")


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------


def varrer_camara(client: httpx.Client) -> Dict[int, Dict[str, Any]]:
    """Todas as proposições das siglas na janela de datas."""
    por_id: Dict[int, Dict[str, Any]] = {}
    for sigla in SIGLAS_CAMARA:
        for pagina in range(1, MAX_PAGINAS + 1):
            r = client.get(URL_CAMARA, params={
                "siglaTipo": sigla, "ano": ANO, "itens": ITENS,
                "ordem": "DESC", "ordenarPor": "id", "pagina": pagina,
            })
            r.raise_for_status()
            dados = r.json().get("dados") or []
            if not dados:
                break
            mais_antigo = None
            for item in dados:
                d = _data(item.get("dataApresentacao"))
                if d and (mais_antigo is None or d < mais_antigo):
                    mais_antigo = d
                pid = item.get("id")
                if not pid or pid in por_id or not (d and JANELA_INI <= d <= JANELA_FIM):
                    continue
                por_id[pid] = {
                    "fonte": "camara", "id_externo": str(pid),
                    "sigla_tipo": item.get("siglaTipo") or sigla,
                    "numero": item.get("numero"), "ano": item.get("ano"),
                    "ementa": (item.get("ementa") or "").strip(),
                    "data_apresentacao": d.isoformat(),
                    "url": URL_FICHA_CAMARA.format(id=pid),
                    "autor": "", "coautores": [],
                }
            if mais_antigo and mais_antigo < JANELA_INI:
                break
            time.sleep(PAUSA_CAMARA)
        print(f"[Câmara] {sigla:4} -> acumulado {len(por_id)}", flush=True)
    return por_id


def varrer_senado(client: httpx.Client) -> Dict[str, Dict[str, Any]]:
    por_codigo: Dict[str, Dict[str, Any]] = {}
    for sigla in SIGLAS_SENADO:
        r = client.get(URL_SENADO, params={"sigla": sigla, "ano": ANO,
                                           "quantidade": 100, "offset": 0})
        r.raise_for_status()
        container = r.json().get("PesquisaBasicaMateria", {}).get("Materias")
        materias = container.get("Materia") if isinstance(container, dict) else container
        if isinstance(materias, dict):
            materias = [materias]
        do_mes = 0
        for m in materias or []:
            d = _data(m.get("Data"))
            if not (d and JANELA_INI <= d <= JANELA_FIM):
                continue
            cod = str(m.get("Codigo") or "")
            if not cod or cod in por_codigo:
                continue
            por_codigo[cod] = {
                "fonte": "senado", "id_externo": cod,
                "sigla_tipo": m.get("Sigla") or sigla,
                "numero": str(m.get("Numero") or "").lstrip("0") or "0",
                "ano": int(m.get("Ano") or ANO),
                "ementa": str(m.get("Ementa") or "").strip(),
                "data_apresentacao": d.isoformat(),
                "url": URL_FICHA_SENADO.format(codigo=cod),
                "autor": str(m.get("Autor") or "").strip(), "coautores": [],
            }
            do_mes += 1
        print(f"[Senado] {sigla:4} -> ano {len(materias or [])}, "
              f"setembro {do_mes}, acumulado {len(por_codigo)}", flush=True)
        time.sleep(PAUSA_SENADO)
    return por_codigo


def enriquecer_autores(client: httpx.Client, itens: List[Dict[str, Any]]) -> None:
    for i, item in enumerate(itens, start=1):
        try:
            r = client.get(f"{URL_CAMARA}/{item['id_externo']}/autores")
            r.raise_for_status()
            dados = r.json().get("dados") or []
            nomes = [str(a.get("nome") or "").strip() for a in dados
                     if isinstance(a, dict) and a.get("nome")]
            item["autor"] = nomes[0] if nomes else ""
            item["coautores"] = nomes[1:]
        except Exception:
            item["autor"] = item.get("autor") or ""
            item["coautores"] = []
        if i % 15 == 0 or i == len(itens):
            print(f"  autores {i}/{len(itens)}", flush=True)
        time.sleep(PAUSA_AUTOR)


def main() -> None:
    with httpx.Client(headers=UA, timeout=60, follow_redirects=True) as client:
        print("=== Câmara: varredura do mês por sigla ===", flush=True)
        camara = varrer_camara(client)
        print(f"Câmara: {len(camara)} proposições em setembro/{ANO}\n", flush=True)

        print("=== Senado ===", flush=True)
        senado = varrer_senado(client)
        print(f"Senado: {len(senado)} proposições em setembro/{ANO}\n", flush=True)

        # Filtra ANTES de enriquecer: a matriz de relevância só precisa da
        # ementa, então buscamos autores apenas das ~2 dezenas de proposições
        # que entram no relatório — e não das ~300 do mês. Preserva o rate
        # limit das APIs (AGENTS.md).
        brutos = list(camara.values()) + list(senado.values())
        selecionados, descartados = [], []
        for item in brutos:
            ok, motivo = relevante(item["ementa"])
            item["subtema"] = subtema(item["ementa"])
            item["motivo_relevancia"] = motivo
            (selecionados if ok else descartados).append(item)

        print(f"\n=== Enriquecendo autores (Câmara, {len(selecionados)} itens) ===",
              flush=True)
        enriquecer_autores(client, [i for i in selecionados if i["fonte"] == "camara"])

    selecionados.sort(key=lambda x: (x["fonte"], x["data_apresentacao"]))
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(
        json.dumps({"janela": [JANELA_INI.isoformat(), JANELA_FIM.isoformat()],
                    "total_bruto": len(brutos),
                    "total_setor": len(selecionados),
                    "proposicoes": selecionados}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    # Dump do bruto em backend/data (fora da pasta de entregas) — usado pela
    # auditoria de falsos negativos e para reclassificar sem re-bater nas APIs.
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "_bruto_educacao_debug.json").write_text(
        json.dumps(brutos, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\nBruto no mês: {len(brutos)} | Setor educação: {len(selecionados)} "
          f"| descartadas: {len(descartados)}")
    print(f"Salvo: {ARQUIVO}")

    from collections import Counter
    cont = Counter(i["subtema"] for i in selecionados)
    print("\nPor sub-tema:")
    for nome, n in cont.most_common():
        print(f"  {n:3}  {nome}")
    print(f"\nPor casa: {Counter(i['fonte'] for i in selecionados)}")


if __name__ == "__main__":
    main()