from fastapi import APIRouter, Query
from typing import Optional
import requests

router = APIRouter(prefix="/eventos", tags=["Eventos e Audiências"])

@router.get("/")
def listar_eventos(
    dataInicio: Optional[str] = Query(None, description="Data inicial no formato AAAA-MM-DD"),
    dataFim: Optional[str] = Query(None, description="Data final no formato AAAA-MM-DD"),
    itens: int = Query(10, ge=1, le=100, description="Quantidade máxima de eventos")
):
    """Busca agenda de reuniões, audiências públicas e eventos na Câmara."""
    url = "https://dadosabertos.camara.leg.br/api/v2/eventos"

    params = {
        "itens": itens,
        "ordem": "asc",
        "ordenarPor": "dataHoraInicio"
    }

    if dataInicio:
        params["dataInicio"] = dataInicio
    if dataFim:
        params["dataFim"] = dataFim

    resposta = requests.get(url, params=params, timeout=(5, 30))

    if resposta.status_code == 200:
        dados = resposta.json()
        eventos_formatados = [
            {
                "id": ev["id"],
                "dataHoraInicio": ev["dataHoraInicio"],
                "descricao": ev["descricao"],
                "descricaoTipo": ev["descricaoTipo"],
                "local": ev.get("localSala", "Local não informado")
            }
            for ev in dados["dados"]
        ]
        return {
            "total": len(eventos_formatados),
            "eventos": eventos_formatados
        }
    else:
        return {"erro": "Não foi possível acessar a API de eventos", "status": resposta.status_code}