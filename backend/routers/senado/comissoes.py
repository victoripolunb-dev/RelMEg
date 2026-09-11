from fastapi import APIRouter
import requests

router = APIRouter(prefix="/senado/comissoes", tags=["Senado - Comissões"])

@router.get("/")
def listar_comissoes_senado():
    """Busca as comissões ativas no Senado Federal."""
    url = "https://legis.senado.leg.br/dadosabertos/comissoes"
    headers = {"Accept": "application/json"}
    
    resposta = requests.get(url, headers=headers, timeout=(5, 30))
    
    if resposta.status_code == 200:
        dados = resposta.json()
        try:
            # Navegando na estrutura da API do Senado
            lista = dados.get("ListaComissoes", {}).get("Comissoes", {}).get("Comissao", [])
            if isinstance(lista, dict):
                lista = [lista]
                
            comissoes_formatadas = [
                {
                    "codigo": c.get("CodigoComissao"),
                    "sigla": c.get("SiglaComissao"),
                    "nome": c.get("NomeComissao"),
                    "casa": c.get("CasaComissao")
                }
                for c in lista
            ]
            return {
                "total": len(comissoes_formatadas),
                "comissoes": comissoes_formatadas
            }
        except Exception as e:
            return {"erro": "Erro ao processar comissões do Senado", "detalhes": str(e)}
    else:
        return {"erro": "Não foi possível acessar a API de comissões do Senado", "status": resposta.status_code}