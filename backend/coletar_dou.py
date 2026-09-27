# -*- coding: utf-8 -*-
"""Recoleta o DOU de 2026-09-15 (seções 1, 2 e 3) — sob demanda.

Mesmos termos da metodologia original; paginação via cursor do portal;
respeito ao rate limit (intervalo >= 6,5s entre requisições ao portal).

Entrega gravada em <dir_entregas>/Relatórios/DOU (AGENTS.md: nunca na raiz do
repositório). Para o fluxo genérico por cliente/tema, use ``monitorar_dou.py``.
"""
import json
import time

from config import settings
from relmeg_core.connectors.dou import coletar_portal_sr

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

# Entrega canônica (AGENTS.md): <dir_entregas>/Relatórios/DOU.
DIR_ENTREGA = settings.dir_relatorios / "DOU"
SAIDA = DIR_ENTREGA / f"dou_energia_{DATA}_com_secao.json"


def main():
    por_secao: dict[int, list[dict]] = {s: [] for s in SECOES}
    for secao in SECOES:
        by_url: dict[str, dict] = {}
        print(f"--- SEÇÃO {secao} ---")
        for q in TERMOS:
            inicio = time.time()
            resultados = coletar_portal_sr(
                q, secao=secao, data=DATA, itens=LIMITE_POR_TERMO
            )
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
    DIR_ENTREGA.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(json.dumps(coletados, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print(f"\nSalvo: {SAIDA} ({len(coletados)} itens únicos — "
          f"S1={len(por_secao[1])} S2={len(por_secao[2])} S3={len(por_secao[3])})")


if __name__ == "__main__":
    main()