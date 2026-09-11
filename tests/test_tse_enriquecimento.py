"""
Testes do ENRIQUECIMENTO TSE (dossiê rico além do payload bruto).

Sem rede: apenas normalização/agregação local sobre mock do detalhe.
Cobre bens individuais + resumo derivado, propostas estruturadas, redes com
plataforma e a ampliação do bloco de dados — preservando compatibilidade.
"""
from servicos.extrator_tse import (
    _bens_individuais,
    _norm_detalhe_rico,
    _norm_propostas,
    _norm_redes,
    _resumo_patrimonio,
    _plataforma_rede,
    _texto_or_none,
)


# ---------------------------------------------------------------------------
# Bens individuais + resumo derivado
# ---------------------------------------------------------------------------

def test_bens_individuais_condicionais_preservam_formato_classico():
    sem_codigo = _bens_individuais(
        {"bens": [{"descricaoDeTipoDeBem": "CASA", "valor": "1000.50"}]}
    )
    assert sem_codigo == [{"tipo": "CASA", "descricao": None, "valor": 1000.5}]

    com_codigo = _bens_individuais({
        "bens": [{"codigoTipoBem": 45, "ordemBem": 3,
                  "descricaoDeTipoDeBem": "TERRENO", "descricao": "Lote", "valor": 500}]
    })
    assert com_codigo == [{
        "tipo": "TERRENO", "descricao": "Lote", "valor": 500.0,
        "codigo_tipo_bem": 45, "ordem": 3,
    }]


def test_resumo_patrimonio_agrega_quantidade_maior_menor_e_por_tipo():
    bens = [
        {"tipo": "CASA", "descricao": None, "valor": 200.0},
        {"tipo": "VEÍCULO", "descricao": None, "valor": 50.0},
        {"tipo": "CASA", "descricao": None, "valor": 0.0},
    ]
    resumo = _resumo_patrimonio(bens)
    assert resumo["quantidade"] == 3
    assert resumo["maiorBem"]["valor"] == 200.0
    assert resumo["menorBem"]["valor"] == 50.0  # ignora item zerado
    assert resumo["distribuicaoPorTipo"] == [
        {"tipo": "CASA", "quantidade": 2, "soma": 200.0},
        {"tipo": "VEÍCULO", "quantidade": 1, "soma": 50.0},
    ]


def test_resumo_patrimonio_vazio():
    resumo = _resumo_patrimonio([])
    assert resumo["quantidade"] == 0
    assert resumo["maiorBem"] is None
    assert resumo["menorBem"] is None
    assert resumo["distribuicaoPorTipo"] == []


def test_resumo_patrimonio_tipo_ausente_vira_nao_informado():
    resumo = _resumo_patrimonio([{"tipo": None, "descricao": None, "valor": 10.0}])
    assert resumo["distribuicaoPorTipo"][0]["tipo"] == "Não informado"
    assert resumo["distribuicaoPorTipo"][0]["soma"] == 10.0


# ---------------------------------------------------------------------------
# Propostas estruturadas
# ---------------------------------------------------------------------------

def test_propostas_normalizam_dicts_e_strings_e_descartam_vazios():
    conteudo = {
        "propostas": [
            {"proposta": "Melhorar a educação"},
            {"titulo": "Saúde", "descricao": "Ampliar UBS"},
            "Plano de mobilidade",
            "",
            12345,
            None,
            {},
        ]
    }
    props = _norm_propostas(conteudo)
    assert len(props) == 3  # dict progirsta, dict título+descrição, string
    # Chaves originais preservadas + título derivado
    primeiro = props[0]
    assert primeiro["proposta"] == "Melhorar a educação"
    assert primeiro["titulo"] == "Melhorar a educação"
    assert primeiro["descricao"] is None
    assert props[1] == {"titulo": "Saúde", "descricao": "Ampliar UBS"}
    assert props[2] == {"titulo": None, "descricao": "Plano de mobilidade"}


def test_propostas_aceitam_plano_de_governo_e_dict_wrapper():
    conteudo = {"propostaGoverno": {"propostas": [{"descricao": "Reforma administrativa"}]}}
    props = _norm_propostas(conteudo)
    assert props == [{"descricao": "Reforma administrativa", "titulo": None}]


# ---------------------------------------------------------------------------
# Redes sociais com plataforma
# ---------------------------------------------------------------------------

def test_plataforma_rede_por_dominio():
    assert _plataforma_rede("https://instagram.com/fulano") == "Instagram"
    assert _plataforma_rede("https://www.facebook.com/x") == "Facebook"
    assert _plataforma_rede("https://x.com/perfil") == "X (Twitter)"
    assert _plataforma_rede("https://twitter.com/perfil") == "X (Twitter)"
    assert _plataforma_rede("https://youtube.com/@canal") == "YouTube"
    assert _plataforma_rede("") is None
    assert _plataforma_rede(None) is None
    assert _plataforma_rede("https://site.com.br") == "Site próprio"


def test_norm_redes_acrescenta_plataforma_e_mantem_titulo_url():
    redes = _norm_redes({
        "redesSociais": [
            {"titulo": "Instagram", "url": "https://instagram.com/fulano"},
            {"titulo": "Site", "url": "http://atalho.so/fulano"},
        ]
    })
    assert redes[0]["plataforma"] == "Instagram"
    assert redes[0]["titulo"] == "Instagram"
    assert redes[1]["url"] == "http://atalho.so/fulano"
    assert redes[1]["plataforma"] is None  # domínio desconhecido


# ---------------------------------------------------------------------------
# Dossiê rico completo (backend-compatível)
# ---------------------------------------------------------------------------

def _conteudo_com_extra():
    return {
        "nomeCompleto": "Fulano de Tal",
        "nomeUrna": "Fulano",
        "nomeSocial": "Fulana",
        "cidadeNatal": "Goiânia",
        "ufNascimento": "GO",
        "cpf": "00000000000",
        "email": "fulano@email.com",
        "ocupacao": "Médico",
        "grauInstrucao": "SUPERIOR",
        "genero": "MASCULINO",
        "corRaca": "PARDA",
        "dataNascimento": "1980-05-12",
        "estadoCivil": "CASADO",
        "descricaoSituacao": "DEFERIDO",
        "totalDeBens": 200.0,
        "bens": [
            {"codigoTipoBem": 80, "descricaoDeTipoDeBem": "CASA", "descricao": "Apartamento", "valor": 150.0},
            {"descricaoDeTipoDeBem": "VEÍCULO", "descricao": "Carro", "valor": 50.0},
        ],
        "propostas": [{"proposta": "Melhorar a educação"}, "Segurança"],
        "redesSociais": [{"titulo": "Instagram", "url": "https://instagram.com/fulano"}],
        "partido": {"sigla": "PSD"},
        "numero": 12345,
        "coligacao": {"nome": "COLIGAÇÃO"},
    }


def test_dossie_rico_agrega_patrimonio_e_amplia_dados():
    rico = _norm_detalhe_rico(_conteudo_com_extra(), None)

    # Patrimônio com resumo derivado + bens detalhados
    pat = rico["patrimonio"]
    assert pat["totalDeBens"] == 200.0
    assert pat["resumo"]["quantidade"] == 2
    assert pat["bens"][0]["codigo_tipo_bem"] == 80
    assert pat["bens"][1] == {"tipo": "VEÍCULO", "descricao": "Carro", "valor": 50.0}
    assert pat["resumo"]["distribuicaoPorTipo"][0]["tipo"] == "CASA"

    # Dados ampliados (defensivos quando ausentes)
    dados = rico["dados"]
    assert dados["nomeSocial"] == "Fulana"
    assert dados["cidadeNatal"] == "Goiânia"
    assert dados["ufNascimento"] == "GO"
    assert dados["email"] == "fulano@email.com"

    # Propostas normalizadas e redes com plataforma
    assert rico["propostas"][0]["titulo"] == "Melhorar a educação"
    assert rico["redesSociais"][0]["plataforma"] == "Instagram"


def test_dossie_rico_campos_ausentes_ficam_none():
    rico = _norm_detalhe_rico({"nomeCompleto": "Só Nome", "bens": []}, None)
    dados = rico["dados"]
    assert dados["nomeSocial"] is None
    assert dados["email"] is None
    assert rico["patrimonio"]["resumo"]["quantidade"] == 0
    assert rico["propostas"] == []
    assert rico["redesSociais"] == []
    assert _texto_or_none(None) is None


def test_dossie_rico_bens_restringe_e_mantem_agregado():
    rico = _norm_detalhe_rico(_conteudo_com_extra(), {"bens"})
    assert "patrimonio" in rico
    assert "dados" not in rico
    assert rico["patrimonio"]["resumo"]["quantidade"] == 2