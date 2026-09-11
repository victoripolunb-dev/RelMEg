from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.requests import Request
from typing import Optional

from rate_limit import limiter, LIMITE_IA

router = APIRouter(prefix="/ai", tags=["IA - Resumo DOU"])


class ResumirDOURequest(BaseModel):
    titulo: Optional[str] = Field(None, max_length=300, description="Título da publicação no DOU")
    texto: str = Field(..., min_length=1, max_length=200_000, description="Texto completo da publicação")


class ResumirDOUResponse(BaseModel):
    titulo: Optional[str]
    resumo: str


@router.post("/resumir-dou", response_model=ResumirDOUResponse)
@limiter.limit(LIMITE_IA)
async def resumir_dou(request: Request, payload: ResumirDOURequest):
    """Gera um resumo executivo de uma publicação do DOU.

    POC: como não há chave de API de LLM configurada, o endpoint devolve um
    resumo determinístico baseado no título e no tamanho do texto. Quando um
    provider real (ex: OpenAI/Claude) for configurado, basta trocar a
    implementação interna.
    """
    texto = (payload.texto or "").strip()
    titulo = (payload.titulo or "").strip()

    palavras = len(texto.split())
    resumo = (
        f"O ato trata de {titulo or 'publicação oficial no Diário Oficial da União'}. "
        f"O texto (cerca de {palavras} palavras) apresenta uma normativa/ato administrativo "
        "com impacto regulatório. Leia o DOU completo em anexo para conferir prazos, "
        "abrangência e eventuais obrigações de compliance."
    )

    return ResumirDOUResponse(titulo=titulo or None, resumo=resumo)