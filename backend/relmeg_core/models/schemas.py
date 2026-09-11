"""
Esquemas universais (Pydantic v2) do RelMeg.

Contrato único entre conectores e o restante do sistema: todo dado extraído —
seja de uma API oficial (JSON/XML) ou de HTML raspado — precisa ser traduzido
para estes modelos antes de chegar ao banco. A persistência nunca deve saber a
origem do dado.

Convenções:
    - Campos obrigatórios usam ``...``; campos opcionais tipados como ``None``.
    - Proveniência (fonte + url_origem + capturado_em) é obrigatória em TODO
      modelo: é o que torna a auditoria e a futura integração BI confiáveis.
    - ``id_externo`` é a chave do órgão de origem (ex.: id da Câmara, código
      do Senado); a unicidade real do registro é a tupla ``(fonte, id_externo)``.
    - Datas chegam normalmente como strings ISO do órgão; o Pydantic coage
      para ``date``. Strings vazias são normalizadas para ``None``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _agora_utc() -> datetime:
    """Timestamp de captura UTC isolado aqui para facilitar testes."""
    return datetime.now(timezone.utc)


class ModeloComProveniencia(BaseModel):
    """Campos comuns de rastreabilidade presentes em todos os modelos."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    # Origem dos dados. Valores canônicos do motor: "camara", "senado",
    # "dou", "ale.mg", "ale.sp", ... (extensível conforme novos conectores).
    fonte: str = Field(..., description="Órgão/fonte de origem dos dados")
    # URL pública consultada (fonte fiel para auditoria e revisão manual).
    url_origem: Optional[str] = Field(
        None, description="URL pública de onde o dado foi extraído"
    )
    # Timestamp UTC de captura. Default preenchido na construção do modelo.
    capturado_em: datetime = Field(default_factory=_agora_utc)


class ParlamentarModel(ModeloComProveniencia):
    """Parlamentar normalizado (deputado, senador ou deputado estadual).

    Obrigatórios: identificador externo, nome completo e a unidade federativa.
    Os demais campos são preenchidos conforme a disponibilidade do órgão.
    """

    id_externo: str = Field(..., min_length=1, description="ID do órgão de origem (ex.: id da Câmara)")
    nome_completo: str = Field(..., min_length=2, description="Nome civil completo")
    partido: Optional[str] = Field(None, description="Sigla do partido atual (ex.: PL)")
    uf: str = Field(..., pattern=r"^[A-Za-z]{2}$", description="UF do mandato (ex.: 'DF')")
    nome_urna: Optional[str] = Field(None, description="Nome parlamentar/urna, quando difere do civil")
    mandato_inicio: Optional[date] = Field(None, description="Início do mandato vigente (AAAA-MM-DD)")
    mandato_fim: Optional[date] = Field(None, description="Fim do mandato vigente (AAAA-MM-DD)")
    status_ativo: bool = Field(True, description="True se o mandato está em exercício")
    cargo: Optional[str] = Field(None, description="Cargo vinculado ao mandato (ex.: Deputado Federal)")
    email: Optional[str] = Field(None, description="E-mail institucional")
    url_foto: Optional[str] = Field(None, description="URL pública da foto oficial")
    url_perfil: Optional[str] = Field(None, description="URL pública do perfil oficial")

    @field_validator("uf", mode="before")
    @classmethod
    def _uf_maiuscula(cls, v):
        """Trata valores nulos/vazios e normaliza a UF para maiúsculas."""
        if v is None or (isinstance(v, str) and not v.strip()):
            raise ValueError("UF é obrigatória")
        return str(v).strip().upper()


class ProjetoDeLeiModel(ModeloComProveniencia):
    """Proposição legislativa (PL, PEC, PLP, MPV, PLS...) normalizada.

    ``integrantes`` lista os autores (parlamentares ou colegiados) em formato
    livre de texto; consumidores que precisarem de relacionamento forte devem
    usar o campo ``autores`` com ``ParlamentarModel`` quando o órgão expuser.
    """

    id_externo: str = Field(..., min_length=1, description="ID/número de registro do órgão de origem")
    sigla_tipo: str = Field(..., min_length=2, description="Tipo normativo (ex.: PL, PEC, PLP, MPV)")
    numero: int = Field(..., ge=0, description="Número da proposição (0 = sem numeração pública na fonte)")
    ano: int = Field(..., ge=0, le=2100, description="Ano de apresentação (0 = indisponível na fonte)")
    ementa: str = Field(..., min_length=5, description="Ementa/ementa(s) resumo da proposição")
    orgao_origem: str = Field(..., min_length=2, description="Casa de origem (ex.: 'camara', 'senado')")
    url_documento: Optional[str] = Field(None, description="URL do documento/inteiro teor")
    autor_principal: Optional[str] = Field(None, description="Nome do autor principal (texto)")
    integrantes: List[str] = Field(default_factory=list, description="Autores/coautores em texto livre")
    pauta_tematica: Optional[str] = Field(None, description="Classificação temática (CPI/Comissão)")
    situacao: Optional[str] = Field(None, description="Situação atual (ex.: 'Aguardando Parecer')")
    comissao_atual: Optional[str] = Field(None, description="Sigla da comissão onde a matéria está")
    relator: Optional[str] = Field(None, description="Nome do relator atual, quando houver")
    data_apresentacao: Optional[date] = Field(None, description="Data de apresentação (AAAA-MM-DD)")
    status_sn: Optional[str] = Field(None, description="Status de tramitação (ex.: 'Ativa')")

    @field_validator("sigla_tipo", "orgao_origem", mode="before")
    @classmethod
    def _sem_espacos(cls, v):
        if v is None:
            return v
        return str(v).strip().upper()


class TramitacaoModel(ModeloComProveniencia):
    """Evento de tramitação de uma proposição (linha do histórico).

    Anomalias de formato do órgão de origem são tratadas nos conectores, nunca
    vazadas para consumidores.
    """

    id_proposicao_externo: str = Field(..., min_length=1, description="id_externo da proposição a que pertence")
    data_evento: Optional[date] = Field(None, description="Data do evento (AAAA-MM-DD)")
    orgao_local: Optional[str] = Field(None, description="Órgão/comissão onde ocorreu (ex.: CCJC)")
    descricao_fase: Optional[str] = Field(None, description="Fase processual descrita (ex.: 'Parecer do Relator')")
    status: str = Field(..., min_length=1, description="Status resumido (ex.: 'Aguardando', 'Aprovado')")
    sequencia: Optional[int] = Field(None, description="Ordem cronológica informada pela casa, quando houver")
    despacho: Optional[str] = Field(None, description="Despacho/decisão do evento, quando disponível")
    url_evento: Optional[str] = Field(None, description="URL pública com detalhes do evento")

    @field_validator("id_proposicao_externo", mode="before")
    @classmethod
    def _id_normalizada(cls, v):
        if v is None:
            return v
        return str(v).strip()