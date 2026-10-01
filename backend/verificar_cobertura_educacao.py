# -*- coding: utf-8 -*-
"""
Cross-check de cobertura: a busca por keywords da Câmara pode perder itens de
setembro. Comparamos com uma varredura INDEPENDENTE por sigla+ordem de id,
filtrando a janela de datas localmente.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from coletar_educacao import (  # noqa: E402
    JANELA_FIM, JANELA_INI, UA, URL_CAMARA, norm, TERMOS,
)

SIGLAS = ["PL", "PEC", "PLP", "PDC", "MPV"]
ITENS = 100


def _d(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def varrer(client, sigla):
    """Todas as proposições da sigla com apresentação na janela (id monotônico)."""
    achados = {}
    for pagina in range(1, 41):
        r = client.get(URL_CAMARA, params={
            "siglaTipo": sigla, "ano": 2026, "itens": ITENS,
            "ordem": "DESC", "ordenarPor": "id", "pagina": pagina,
        })
        r.raise_for_status()
        dados = r.json().get("dados") or []
        if not dados:
            break
        mais_antigo = None
        for it in dados:
            d = _d(it.get("dataApresentacao"))
            if d and (mais_antigo is None or d < mais_antigo):
                mais_antigo = d
            if d and JANELA_INI <= d <= JANELA_FIM:
                achados[it["id"]] = it
        if mais_antigo and mais_antigo < JANELA_INI:
            break
        time.sleep(0.6)
    return achados


def main():
    termos_norm = {norm(t) for t in TERMOS}
    # termos de contexto implícitos que a busca por keyword tenderia a perder
    contexto = {"educacao", "ensino", "escola", "escolar", "estudante", "estudantil",
                "professor", "professora", "docente", "universitari", "vestibular",
                "matricula", "creche", "infantil", "pedagog", "didatic", "currulo",
                "academico", "mec", "inep", "fundeb", "prouni", "fies", "enem",
                "enamed", "ead", "medicina", "saude", "lotacao", "vaga"}
    alvo = termos_norm | contexto

    cobertura = {}
    with httpx.Client(headers=UA, timeout=60, follow_redirects=True) as client:
        for sigla in SIGLAS:
            itens = varrer(client, sigla)
            print(f"{sigla}: {len(itens)} na janela", flush=True)
            cobertura.update(itens)

    print(f"\nTOTAL varrido na janela: {len(cobertura)}")

    # quais tocam o setor educação?
    do_setor = {}
    for pid, it in cobertura.items():
        txt = norm(f"{it.get('ementa') or ''} {it.get('siglaTipo') or ''}")
        if any(t in txt for t in alvo):
            do_setor[pid] = it
    print(f"Do setor educação (busca local ampla): {len(do_setor)}")

    # comparar com o que a coleta por keywords entregou
    coletas = Path(
        settings_dir := (Path.home() / "Desktop" / "RelMeg - Entregas" / "Novas proposições")
    ) / "proposicoes_educacao_setembro_2026.json"
    if coletas.exists():
        itens = json.loads(coletas.read_text(encoding="utf-8"))
        por_id = {i["id_externo"] for i in itens if i["fonte"] == "camara"}
        print(f"\nColeta por keywords entregou (Câmara): {len(por_id)}")
        faltando = set(map(str, do_setor)) - por_id
        print(f"PERDIDOS pela busca por keywords: {len(faltando)}")
        for pid in list(faltando)[:25]:
            it = do_setor[int(pid)]
            print(f"  {it.get('siglaTipo')} {it.get('numero')} "
                  f"({str(it.get('dataApresentacao'))[:10]}) "
                  f"{(it.get('ementa') or '')[:85]}")


if __name__ == "__main__":
    main()