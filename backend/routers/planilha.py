import base64
import csv
import io
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from starlette.requests import Request

from rate_limit import limiter, LIMITE_UPLOAD

router = APIRouter(prefix="/api", tags=["Upload de planilhas e exportação BI"])

COLUNAS_PADRAO = ["nome", "partido", "uf", "cargo", "interesse1", "contrario1", "setor1", "descricao"]

# Blindagem de uploads: extensões estritamente permitidas e teto de 10MB
EXTENSOES_PERMITIDAS = {"csv", "xlsx", "pdf"}
LIMITE_TAMANHO_BYTES = 10 * 1024 * 1024  # 10 MB
LIMITE_LINHAS_CSV = 100_000
LIMITE_LINHAS_EXPORTACAO = 100_000
LIMITE_TAMANHO_DESCOMPRIMIDO_XLSX = 512 * 1024 * 1024  # 512 MB descomprimido (anti zip-bomb)


def _extensao_de(nome_arquivo: str) -> str:
    return (nome_arquivo or "").rsplit(".", 1)[-1].lower() if "." in (nome_arquivo or "") else ""


def _neutralizar_formula(valor: str) -> str:
    """Impede formula injection ao exportar CSV para Excel/Looker.

    Células cujo texto inicia com = + - @ passariam a ser interpretadas como
    fórmulas pelo Excel/Sheets ao abrir/pastar o arquivo. Prefixa uma aspa
    simples neutra, preservando o conteúdo exibido.
    """
    if len(valor) > 1 and valor.startswith(("=", "+", "-", "@")):
        return "'" + valor
    return valor


async def _ler_com_limite(file: UploadFile, limite: int) -> bytes:
    """Lê o upload lendo apenas `limite + 1` bytes, evitando esgotar memória.

    Arquivos maiores que o teto são rejeitados com 413 sem jamais serem
    integralmente carregados em memória.
    """
    conteudo = await file.read(limite + 1)
    if len(conteudo) > limite:
        raise HTTPException(
            status_code=413,
            detail=f"Arquivo excede o limite de {limite // (1024 * 1024)} MB.",
        )
    return conteudo


def _ler_xlsx(conteudo: bytes) -> List[Dict[str, Any]]:
    try:
        import openpyxl
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="O processamento de .xlsx no backend exige a dependência 'openpyxl'. Instale com 'pip install openpyxl'.",
        ) from exc

    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as zf:
            total_descomprimido = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=422,
            detail="Arquivo .xlsx inválido: não é um ZIP válido.",
        ) from exc
    if total_descomprimido > LIMITE_TAMANHO_DESCOMPRIMIDO_XLSX:
        raise HTTPException(
            status_code=413,
            detail="Planilha .xlsx descomprime além do limite suportado pelo backend.",
        )

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException) as exc:
        raise HTTPException(
            status_code=422,
            detail="Arquivo .xlsx corrompido ou em formato inválido.",
        ) from exc
    planilha = workbook.active
    linhas = planilha.iter_rows(values_only=True)
    cabecalho = None
    registros: List[Dict[str, Any]] = []
    for index, linha in enumerate(linhas):
        if index == 0:
            cabecalho = [str(c or "").strip().lower() if c is not None else "" for c in linha]
            continue
        if linha is None:
            continue
        # Neutraliza fórmula antes de devolver ao cliente: células iniciadas
        # com =,+,-,@ virariam comandos ao serem coladas em Excel/Sheets.
        valores = [
            _neutralizar_formula(str(c or "").strip()) if c is not None else ""
            for c in linha
        ]
        registro = {cabecalho[i]: v for i, v in enumerate(valores) if i < len(cabecalho)}
        if any(registro.values()):
            registros.append(registro)
    return registros


def _detectar_delimitador(texto: str) -> str:
    """Sniff do delimitador de um CSV (`,` ou `;`).

    Usa o ``csv.Sniffer`` da stdlib; se a amostra for ambígua/insuficiente,
    cai no padrão ``;`` (gerado pelas exportações do próprio RelMeg).
    """
    amostra = texto[:4096]
    try:
        s = csv.Sniffer()
        if s.has_header(amostra):
            return s.sniff(amostra, delimiters=";,").delimiter
    except csv.Error:
        pass
    for delimitador in (",", ";"):
        if amostra.count(delimitador) > 0:
            return delimitador
    return ";"


def _ler_csv(conteudo: bytes) -> List[Dict[str, Any]]:
    texto = conteudo.decode("utf-8-sig", errors="replace")
    delimitador = _detectar_delimitador(texto)
    leitor = csv.DictReader(io.StringIO(texto), delimiter=delimitador)
    registros: List[Dict[str, Any]] = []
    for indice, linha in enumerate(leitor):
        if indice >= LIMITE_LINHAS_CSV:
            raise HTTPException(
                status_code=413,
                detail=f"Planilha com mais de {LIMITE_LINHAS_CSV} linhas não é suportada.",
            )
        normalizado = {
            str(k or "").strip().lower(): _neutralizar_formula(str(v or "").strip())
            for k, v in linha.items()
        }
        if any(normalizado.values()):
            registros.append(normalizado)
    return registros


@router.post("/upload/planilha")
@limiter.limit(LIMITE_UPLOAD)
async def upload_planilha(
    request: Request,
    file: UploadFile = File(..., description="Arquivo .csv, .xlsx ou .pdf"),
    cliente: Optional[str] = Form(None, description="Chave do cliente/projeto ativo"),
):
    """Recebe planilhas (.csv/.xlsx) ou PDF e devolve os registros normalizados.

    - .csv → parsing com a biblioteca padrão (`;` ou `,`)
    - .xlsx → parsing via openpyxl (opcional)
    - .pdf → armazenado como anexo base64 do projeto ativo

    Validações de segurança: extensão estritamente `.csv/.xlsx/.pdf` e
    tamanho máximo de 10 MB (413 se exceder).
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")

    extensao = _extensao_de(file.filename or "")
    if extensao not in EXTENSOES_PERMITIDAS:
        raise HTTPException(
            status_code=400,
            detail="Formato não suportado. Use estritamente .csv, .xlsx ou .pdf.",
        )

    conteudo = await _ler_com_limite(file, LIMITE_TAMANHO_BYTES)

    if extensao == "csv":
        registros = _ler_csv(conteudo)
        return {"arquivo": file.filename, "formato": "csv", "cliente": cliente, "total": len(registros), "registros": registros}

    if extensao == "xlsx":
        registros = _ler_xlsx(conteudo)
        return {"arquivo": file.filename, "formato": "xlsx", "cliente": cliente, "total": len(registros), "registros": registros}

    anexo = {
        "id": f"anexo-{base64.urlsafe_b64encode(file.filename.encode()).decode()[:12]}",
        "cliente": cliente,
        "nome": file.filename,
        "tipo": "pdf",
        "tamanho_bytes": len(conteudo),
        "conteudo_base64": base64.b64encode(conteudo).decode(),
    }
    return {"arquivo": file.filename, "formato": "pdf", "cliente": cliente, "anexo": anexo}


@router.post("/exportar/sheets")
@limiter.limit(LIMITE_UPLOAD)
async def exportar_sheets(request: Request, payload: Dict[str, Any]):
    """Ponte para Google Sheets / Looker Studio.

    Recebe `{"nome": "...", "registros": [{...}]}` e devolve um CSV pronto
    para colar no Google Sheets (onde o Looker Studio se conecta).
    """
    nome = str(payload.get("nome") or "relmeg-exportacao")
    registros: List[Dict[str, Any]] = payload.get("registros") or []
    if not isinstance(registros, list):
        raise HTTPException(status_code=400, detail="O campo 'registros' precisa ser uma lista.")

    if len(registros) > LIMITE_LINHAS_EXPORTACAO:
        raise HTTPException(
            status_code=413,
            detail=f"Exportação com mais de {LIMITE_LINHAS_EXPORTACAO} registros não é suportada.",
        )

    colunas: List[str] = []
    for registro in registros:
        if isinstance(registro, dict):
            for chave in registro.keys():
                if str(chave) not in colunas:
                    colunas.append(str(chave))

    if not colunas:
        return {"nome": nome, "csv": "", "colunas": [], "total": 0}

    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    escritor.writerow(colunas)
    for registro in registros:
        if isinstance(registro, dict):
            escritor.writerow([_neutralizar_formula(str(registro.get(c, "") or "")) for c in colunas])

    total_caracteres = len(saida.getvalue())
    if total_caracteres > 5_000_000:
        raise HTTPException(
            status_code=413,
            detail="Exportação grande demais para o formato CSV (5 MB de texto).",
        )

    return {
        "nome": nome,
        "csv": saida.getvalue(),
        "colunas": colunas,
        "total": len(registros),
        "ponte": "Cole o CSV em uma planilha do Google Sheets para conectar ao Looker Studio (via URL pública ou BigQuery).",
    }