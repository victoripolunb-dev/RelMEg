from fastapi import APIRouter, Query
import requests

router = APIRouter(prefix="/frentes", tags=["Frentes Parlamentares"])

@router.get("/")
def listar_frentes(
    itens: int = Query(10, ge=1, le=100, description="Quantidade máxima de frentes retornadas")
):
    """Busca as frentes parlamentares ativas na Câmara dos Deputados."""
    url = "https://dadosabertos.camara.leg.br/api/v2/frentes"
    
    params = {"itens": itens}
    resposta = requests.get(url, params=params, timeout=(5, 30))
    
    if resposta.status_code == 200:
        dados = resposta.json()
        frentes_formatadas = [
            {
                "id": f["id"],
                "titulo": f["titulo"],
                "idLegislatura": f.get("idLegislatura")
            }
            for f in dados.get("dados", [])
        ]
        return {
            "total": len(frentes_formatadas),
            "frentes": frentes_formatadas
        }
    else:
        return {"erro": "Não foi possível acessar a API de frentes parlamentares", "status": resposta.status_code}


@router.get("/{id}/membros")
def listar_membros_frente(id: int):
    """Busca os deputados membros de uma frente parlamentar específica pelo ID."""
    url = f"https://dadosabertos.camara.leg.br/api/v2/frentes/{id}/membros"
    
    resposta = requests.get(url, timeout=(5, 30))
    
    if resposta.status_code == 200:
        dados = resposta.json()
        membros_formatados = [
            {
                "nome": m.get("nome"),
                "cargo": m.get("titulo"),
                "partido": m.get("siglaPartido"),
                "uf": m.get("siglaUf")
            }
            for m in dados.get("dados", [])
        ]
        return {
            "frente_id": id,
            "total_membros": len(membros_formatados),
            "membros": membros_formatados
        }
    else:
        return {"erro": "Não foi possível buscar os membros desta frente", "status": resposta.status_code}