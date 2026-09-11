from fastapi import APIRouter
import requests

router = APIRouter(prefix="/proposicoes", tags=["Autores e Relatores"])

@router.get("/{id}/autores")
def listar_autores_proposicao(id: int):
    """Busca os autores de uma proposição específica pelo ID."""
    url = f"https://dadosabertos.camara.leg.br/api/v2/proposicoes/{id}/autores"
    
    resposta = requests.get(url, timeout=(5, 30))
    
    if resposta.status_code == 200:
        dados = resposta.json()
        autores_formatados = [
            {
                "nome": autor.get("nome"),
                "tipo": autor.get("tipo"),
                "partido": autor.get("siglaPartido"),
                "uf": autor.get("siglaUf")
            }
            for autor in dados.get("dados", [])
        ]
        return {
            "proposicao_id": id,
            "total_autores": len(autores_formatados),
            "autores": autores_formatados
        }
    else:
        return {"erro": "Não foi possível buscar os autores desta proposição", "status": resposta.status_code}