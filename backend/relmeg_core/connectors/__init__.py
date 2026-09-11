"""Conectores por órgão (Adapter). A base abstrata vive em base_connector.py.

V1 — escopo aprovado (Câmara, Senado, CLDF, DOU). ALEs além da CLDF ficam
registradas como mapeamento futuro; ALGO sem API pública:
    - camara.py  → caminho feliz (API Dados Abertos v2, JSON/XML)
    - senado.py  → API/XML oficial do Senado
    - cldf.py    → Câmara Legislativa do Distrito Federal (piloto ALE)
    - dou.py     → Diário Oficial da União (fonte de BUSCA via portal SR)
    - algo.py    → Assembléia de Goiás (sem API pública — esqueleto)
    - almg.py    → API 'Dados Abertos' v2 existe → mapeamento futuro
    - alesp.py   → dados em CSV/RDF (bulk) → mapeamento futuro
"""
from relmeg_core.connectors.base_connector import (
    FonteSemApiPublica,
    LegislativoConnector,
    filtrar_campos,
)
from relmeg_core.connectors.alesp import AlespConnector
from relmeg_core.connectors.algo import AlgoConnector
from relmeg_core.connectors.almg import AlmgConnector
from relmeg_core.connectors.camara import CamaraConnector
from relmeg_core.connectors.cldf import CldfConnector
from relmeg_core.connectors.dou import DouConnector, DOUSemFichaEstruturada
from relmeg_core.connectors.senado import SenadoConnector

__all__ = [
    "AlespConnector",
    "AlgoConnector",
    "AlmgConnector",
    "CamaraConnector",
    "CldfConnector",
    "DOUSemFichaEstruturada",
    "DouConnector",
    "FonteSemApiPublica",
    "LegislativoConnector",
    "SenadoConnector",
    "filtrar_campos",
]