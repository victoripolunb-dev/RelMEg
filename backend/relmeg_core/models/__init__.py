"""Modelos universais (Pydantic) usados por todos os conectores do RelMeg."""
from relmeg_core.models.schemas import (
    ParlamentarModel,
    ProjetoDeLeiModel,
    TramitacaoModel,
    ModeloComProveniencia,
)

__all__ = [
    "ModeloComProveniencia",
    "ParlamentarModel",
    "ProjetoDeLeiModel",
    "TramitacaoModel",
]