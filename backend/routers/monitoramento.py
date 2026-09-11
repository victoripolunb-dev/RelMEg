from fastapi import APIRouter, Query
from starlette.requests import Request
from typing import Optional
import requests

from rate_limit import limiter, LIMITE_MONITORAMENTO

router = APIRouter(prefix="/monitoramento", tags=["Monitoramento Setorial"])

@router.get("/camara")
@limiter.limit(LIMITE_MONITORAMENTO)
def monitorar_camara(
    request: Request,
    q: str = Query(..., min_length=3, max_length=120, description="Palavra-chave para buscar nas ementas, ex: energia, tarifa, marco regulatório"),
    ano: int = Query(2026, ge=1900, le=2100, description="Ano das proposições"),
    itens: int = Query(10, ge=1, le=50, description="Quantidade máxima de resultados")
):
    """Busca proposições na Câmara filtrando por palavras-chave na ementa ou texto."""
    url = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
    
    params = {
        "ano": ano,
        "keywords": q,
        "itens": itens,
        "ordem": "desc",
        "ordenarPor": "id"
    }
    
    resposta = requests.get(url, params=params, timeout=(5, 30))
    
    if resposta.status_code == 200:
        dados = resposta.json()
        proposicoes_formatadas = [
            {
                "id": p["id"],
                "uri": p["uri"],
                "siglaTipo": p["siglaTipo"],
                "numero": p["numero"],
                "ano": p["ano"],
                "ementa": p["ementa"]
            }
            for p in dados.get("dados", [])
        ]
        return {
            "termo_pesquisado": q,
            "total": len(proposicoes_formatadas),
            "resultados": proposicoes_formatadas
        }
    else:
        return {"erro": "Não foi possível realizar o monitoramento na Câmara", "status": resposta.status_code}


@router.get("/senado")
@limiter.limit(LIMITE_MONITORAMENTO)
def monitorar_senado(
    request: Request,
    q: str = Query(..., min_length=3, max_length=120, description="Palavra-chave para buscar nas matérias do Senado, ex: energia, marco legal"),
    ano: Optional[int] = Query(2026, ge=1900, le=2100, description="Ano da matéria")
):
    """Busca matérias no Senado filtrando por palavra-chave na ementa."""
    url = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"
    headers = {"Accept": "application/json"}
    
    params = {}
    if ano:
        params["ano"] = ano
        
    resposta = requests.get(url, params=params, headers=headers, timeout=(5, 30))
    
    if resposta.status_code == 200:
        dados = resposta.json()
        try:
            pesquisa = dados.get("PesquisaBasicaMateria", {})
            lista_materias = pesquisa.get("Materias", {}).get("Materia", [])
            
            if isinstance(lista_materias, dict):
                lista_materias = [lista_materias]
                
            # Filtro inteligente por palavra-chave na ementa (case insensitive)
            termo = q.lower()
            filtradas = []
            
            for m in lista_materias:
                ementa = m.get("Ementa", "") or ""
                if termo in ementa.lower():
                    filtradas.append({
                        "codigo": m.get("Codigo"),
                        "sigla": m.get("Sigla"),
                        "tipo": m.get("DescricaoIdentificacao"),
                        "numero": m.get("Numero"),
                        "ano": m.get("Ano"),
                        "ementa": ementa,
                        "autor": m.get("Autor"),
                        "data": m.get("Data"),
                        "url": m.get("UrlDetalheMateria")
                    })
                    
            return {
                "termo_pesquisado": q,
                "total": len(filtradas),
                "resultados": filtradas
            }
        except Exception as e:
            return {"erro": "Erro ao processar filtro do Senado", "detalhes": str(e)}
    else:
        return {"erro": "Não foi possível acessar a API de monitoramento do Senado", "status": resposta.status_code}