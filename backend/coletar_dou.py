# -*- coding: utf-8 -*-
"""Recoleta o DOU de 2026-09-15 restrito à Seção 1 (s=do1) — sob demanda.

Mesmos termos da metodologia original; paginação via cursor do portal;
respeito ao rate limit (intervalo >= 6,5s entre requisições ao portal).
"""
import json
import time
from pathlib import Path

from routers.dou import _coletar_portal

DATA = "2026-09-15"
SECOES = (1, 2, 3)
LIMITE_POR_TERMO = 200
INTERVALO = 6.5

TERMOS = [
    "ABRADEE", "ABEEólica", "ABiogás", "ABIAPE", "ABRAGE", "ABRATE",
    "Renova Energia", "ANEEL", "energia elétrica", "concessão de energia",
    "leilão de energia", "distribuidora de energia", "energia eólica",
    "energia solar", "biogás", "biomassa", "hidrogênio", "tarifa de energia",
    "mercado livre de energia", "Ministério de Minas e Energia",
    # termos de suporte
    "gás natural", "petróleo", "combustíveis", "usina", "transmissão elétrica",
]

SAIDA = Path(__file__).parent / f"dou_energia_{DATA}_secao{1}.json"


SAIDA = Path(__file__).parent / f"dou_energia_{DATA}_com_secao.json"


def main():
    por_secao: dict[int, list[dict]] = {s: [] for s in SECOES}
    for secao in SECOES:
        by_url: dict[str, dict] = {}
        print(f"--- SEÇÃO {secao} ---")
        for q in TERMOS:
            inicio = time.time()
            resultados = _coletar_portal(q, secao, DATA, limite=LIMITE_POR_TERMO)
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

    coletados = (por_secao[1] + por_secao[2] + por_secao[3])
    SAIDA.write_text(json.dumps(coletados, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"\nSalvo: {SAIDA} ({len(coletados)} itens únicos — "
          f"S1={len(por_secao[1])} S2={len(por_secao[2])} S3={len(por_secao[3])})")


if __name__ == "__main__":
    main()