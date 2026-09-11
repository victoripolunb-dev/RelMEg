from fastapi import APIRouter
import requests

router = APIRouter(prefix="/deputados", tags=["Deputados"])

@router.get("/")
def listar_deputados(itens: int = 3):
    """Busca os primeiros parlamentares na API oficial da Câmara dos Deputados."""
    url = f"https://dadosabertos.camara.leg.br/api/v2/deputados?itens={itens}"

    resposta = requests.get(url, timeout=(5, 30))

    if resposta.status_code == 200:
        dados = resposta.json()
        deputados_formatados = [
            {
                "nome": dep["nome"],
                "partido": dep["siglaPartido"],
                "uf": dep["siglaUf"]
            }
            for dep in dados["dados"]
        ]
        return {"total": len(deputados_formatados), "deputados": deputados_formatados}
    else:
        return {"erro": "Não foi possível acessar a API da Câmara", "status": resposta.status_code}