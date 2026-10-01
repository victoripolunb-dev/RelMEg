# -*- coding: utf-8 -*-
"""
Nova proposição — SETOR DE EDUCAÇÃO — complemento: situação de tramitação.

Enriquece o JSON coletado por ``coletar_educacao.py`` com a última situação
registrada em cada Casa (Câmara via ``statusProposicao``, Senado via última
movimentação da matéria), mais comissão atual e relator.

Uso (sob demanda, nunca automático):
    .venv\\Scripts\\python.exe enriquecer_educacao.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coletar_educacao import ARQUIVO  # noqa: E402
from relmeg_core.connectors.camara import CamaraConnector  # noqa: E402
from relmeg_core.connectors.senado import SenadoConnector  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

# Requisições dentro de uma mesma proposição (asyncio.gather) são permitidas
# pela diretriz: é concorrência de chamadas HTTP em uma única requisição
# on-demand do operador, não processamento em segundo plano.
PAUSA = 0.8


async def enriquecer_camara(conector: CamaraConnector, itens: list[dict]) -> None:
    for item in itens:
        try:
            projeto = await conector.obter_projeto(item["id_externo"])
            item["situacao"] = projeto.situacao
            item["comissao_atual"] = projeto.comissao_atual
            item["relator"] = projeto.relator
            item["status_sn"] = projeto.status_sn
        except Exception as exc:  # noqa: BLE001
            item["situacao"] = f"indisponível ({type(exc).__name__})"
        print(f"  [Câmara] {item['sigla_tipo']} {item['numero']}/2026 "
              f"-> {item.get('situacao')}", flush=True)
        await asyncio.sleep(PAUSA)


async def enriquecer_senado(conector: SenadoConnector, itens: list[dict]) -> None:
    for item in itens:
        try:
            projeto = await conector.obter_projeto(item["id_externo"])
            item["situacao"] = projeto.situacao
            item["comissao_atual"] = projeto.comissao_atual
            item["relator"] = projeto.relator
            item["status_sn"] = projeto.status_sn
        except Exception as exc:  # noqa: BLE001
            item["situacao"] = f"indisponível ({type(exc).__name__})"
        print(f"  [Senado] {item['sigla_tipo']} {item['numero']}/2026 "
              f"-> {item.get('situacao')}", flush=True)
        await asyncio.sleep(PAUSA)


async def main() -> None:
    payload = json.loads(ARQUIVO.read_text(encoding="utf-8"))
    itens = payload["proposicoes"]
    print(f"Enriquecendo {len(itens)} proposições com situação de tramitação\n")

    await enriquecer_camara(CamaraConnector(), [i for i in itens if i["fonte"] == "camara"])
    await enriquecer_senado(SenadoConnector(), [i for i in itens if i["fonte"] == "senado"])

    for item in itens:
        item.setdefault("situacao", None)
        item.setdefault("comissao_atual", None)
        item.setdefault("relator", None)

    ARQUIVO.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    sem_situacao = [i for i in itens if not i.get("situacao")]
    print(f"\nSalvo: {ARQUIVO}")
    print(f"Com situação: {len(itens) - len(sem_situacao)}/{len(itens)}")
    for i in sem_situacao:
        print(f"  sem situação: {i['sigla_tipo']} {i['numero']}/2026")


if __name__ == "__main__":
    asyncio.run(main())