"""
Wrapper isolado do Scrapling — estepe de contingência do motor.

Importado APENAS quando uma API oficial falha (403/503/timeout) ou quando o
órgão não expõe API e exige raspagem (ex.: portais arcaicos de ALEs).

Mantido 100% independente (stdlib + ``scrapling`` lazy) para que:
    - o pacote ``relmeg_core`` importe sem custo (sem pydantic/httpx aqui);
    - falha de importação aqui NÃO derrube a aplicação;
    - usuários sem o Scrapling instalado só percebam isso ao cair no fallback.

Uso (dentro de um conector):
    dados = extrair_html_seguro(url, seletores={...})

Formato do dicionário ``seletores`` (CSS ou XPath, com modo adaptivo):
    {
      "titulo": "h1",                        # 1º match, texto
      "num":    {"css": "#processo"},        # 1º match, texto (forma explícita)
      "links":  {"css": "a", "attr": "href", "lista": True},   # todos os hrefs
      "da":     {"xpath": "//span[@id='data']/text()"},
    }

O ``adaptive=True`` (padrão) reaproveita a heurística do Scrapling para
manter a extração viva mesmo se a classe CSS do portal mudar.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Union

# Shadowing proposital: reatribuído após o import lazy bem-sucedido.
StealthyFetcher = None  # type: ignore[assignment]

_MENSAGEM_MISSING = (
    "Scrapling não está instalado. O fallback por raspagem exige "
    "`pip install scrapling` (ver backend/requirements-extras.txt)."
)


def _carregar_scrapling() -> Any:
    """Importa o StealthyFetcher sob demanda (lazy). Levanta erro claro se faltar."""
    global StealthyFetcher
    if StealthyFetcher is not None:
        return StealthyFetcher
    try:
        from scrapling import StealthyFetcher as _sf

        StealthyFetcher = _sf
        return _sf
    except ImportError as exc:
        raise RuntimeError(_MENSAGEM_MISSING) from exc


def _fechar_fetcher(fetcher: Any) -> None:
    """Encerra o browser/contexto headless (evita processos zumbis do Playwright).

    O ``close()`` do Scrapling não é chamado automaticamente pelo ``fetch``; sem
    ele, cada raspagem deixaria um browser órfão em memória/socket. Best-effort:
    se a API do Scrapling mudar de nome, seguimos sem quebrar o fluxo.
    """
    fechar = getattr(fetcher, "close", None)
    if callable(fechar):
        try:
            fechar()
        except Exception:  # noqa: BLE001
            return


def _seletor_expressao(config: Union[str, Mapping[str, Any]]) -> tuple:
    """Normaliza a config de um campo em ``(expressao, kind)``."""
    if isinstance(config, str):
        return config, "css"
    plano = config or {}
    if "xpath" in plano:
        return str(plano["xpath"]), "xpath"
    return str(plano.get("css", "")), "css"


def _texto_do_match(match: Any) -> Optional[str]:
    """Extrai texto de um Selector do Scrapling de forma tolerante."""
    if match is None:
        return None
    try:
        valor = match.text
        return str(valor).strip() if valor is not None else None
    except Exception:
        return None


def _valor_campo(resposta: Any, config: Union[str, Mapping[str, Any]]) -> Any:
    """Aplica a config sobre a Response e devolve o valor de um campo."""
    plano = config if isinstance(config, dict) else {}
    expressao, kind = _seletor_expressao(config)
    attr = plano.get("attr")
    lista = bool(plano.get("lista"))

    if kind == "xpath":
        matches = resposta.xpath(expressao, adaptive=True)
    else:
        matches = resposta.css(expressao, adaptive=True)

    if lista:
        valores = []
        for m in matches:
            if m is None:
                continue
            if attr:
                v = m.attrib.get(attr)
            else:
                v = _texto_do_match(m)
            if v not in (None, ""):
                valores.append(str(v).strip() if not isinstance(v, str) else v.strip())
        return valores

    primeiro = matches.first if hasattr(matches, "first") else None
    if primeiro is None and len(matches) > 0:
        primeiro = matches[0]
    if primeiro is None:
        return None
    if attr:
        return primeiro.attrib.get(attr)
    return _texto_do_match(primeiro)


def extrair_html_seguro(
    url: str,
    seletores: Dict[str, Union[str, Mapping[str, Any]]],
    *,
    adaptivo: bool = True,
    timeout_ms: int = 30_000,
    headless: bool = True,
) -> Dict[str, Any]:
    """Raspa uma URL pública e devolve dict na forma ``{campo: valor}``.

    Dissigna o ``adaptive`` global do Scrapling se ``adaptivo=True`` (default),
    para que a heurística *self-healing* atue também no nível do parser.

    Nota: ``timeout_ms`` é a tolerância do browser Stealth (em MILISSEGUNDOS).

    Levanta:
        - RuntimeError  se o Scrapling não estiver instalado;
        - ConnectionError se o status HTTP for >= 400;
        - Exception     se o parser devolver conteúdo inesperado (o conector
          decide se propaga ou aplica outra estratégia).
    """
    fetcher = _carregar_scrapling()
    try:
        if adaptivo:
            fetcher.configure(adaptive=True)

        resposta = fetcher.fetch(url, timeout=timeout_ms, headless=headless)

        status = getattr(resposta, "status", None)
        if status is not None and status >= 400:
            raise ConnectionError(f"Scrapling devolveu HTTP {status} para {url}")

        return {campo: _valor_campo(resposta, config) for campo, config in seletores.items()}
    finally:
        _fechar_fetcher(fetcher)


def extrair_html_simples(
    url: str,
    seletores: Dict[str, Union[str, Mapping[str, Any]]],
    *,
    timeout_ms: int = 30_000,
    headless: bool = True,
) -> Dict[str, Any]:
    """Versão conveniente: um clique no fallback sem opções de adaptive.

    Equivalente a chamar ``extrair_html_seguro(..., adaptivo=True)``.
    """
    return extrair_html_seguro(
        url,
        seletores,
        adaptivo=True,
        timeout_ms=timeout_ms,
        headless=headless,
    )