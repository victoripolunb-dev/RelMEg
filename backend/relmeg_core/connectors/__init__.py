"""Conectores por órgão (Adapter). A base abstrata vive em base_connector.py.

Escopo vigente — esfera federal apenas (Câmara, Senado, DOU). Os conectores
estaduais (CLDF, ALGO, ALMG, ALESP) foram removidos pela decisão do operador
em 01/10/2026. Conectores disponíveis:
    - camara.py  → caminho feliz (API Dados Abertos v2, JSON/XML)
    - senado.py  → API/XML oficial do Senado
    - dou.py     → Diário Oficial da União (fonte de BUSCA via portal SR)

Se uma fonte estadual voltar ao escopo, o contrato do Adapter segue o mesmo:
novo arquivo em ``connectors/`` + registro em ``REGISTRO_CONECTORES``.
"""
from relmeg_core.connectors.base_connector import (
    FonteSemApiPublica,
    LegislativoConnector,
    filtrar_campos,
)
from relmeg_core.connectors.camara import CamaraConnector
from relmeg_core.connectors.dou import DouConnector, DOUSemFichaEstruturada
from relmeg_core.connectors.senado import SenadoConnector

__all__ = [
    "CamaraConnector",
    "DOUSemFichaEstruturada",
    "DouConnector",
    "FonteSemApiPublica",
    "LegislativoConnector",
    "SenadoConnector",
    "filtrar_campos",
]