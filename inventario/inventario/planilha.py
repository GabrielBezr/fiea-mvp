"""Leitura de planilhas do administrador, tolerante ao formato de origem.

A relacao patrimonial vem da FIEA, nao do nosso aplicativo: os cabecalhos variam
("Patrimonio", "N. Patrimonio", "TOMBO"), tem acento, maiuscula e espaco a mais.
O risco "formato heterogeneo" da secao 5.1 se resolve aqui, normalizando o
cabecalho e aceitando sinonimos, em vez de exigir um arquivo exato.

CSV sai da biblioteca padrao. XLSX exige openpyxl; sem ele, a mensagem diz o que
fazer em vez de estourar um ImportError.
"""

import csv
import unicodedata
from pathlib import Path


class PlanilhaInvalida(Exception):
    """Planilha ilegivel ou sem as colunas necessarias."""


# Indicadores ordinais sao alfanumericos para o Python, entao "N.º Patrimonio"
# viraria "n o patrimonio" e nao casaria com sinonimo nenhum. Viram separador.
ORDINAIS = str.maketrans({"º": " ", "ª": " ", "°": " ", "№": " "})


def normalizar(texto):
    """'N.º  Patrimônio ' -> 'n patrimonio'. Base da comparacao de cabecalhos."""
    if texto is None:
        return ""
    sem_ordinal = str(texto).translate(ORDINAIS)
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", sem_ordinal)
        if unicodedata.category(c) != "Mn"
    )
    limpo = "".join(c if c.isalnum() else " " for c in sem_acento.lower())
    return " ".join(limpo.split())


def ler_linhas(caminho):
    """Devolve (cabecalho_normalizado, linhas) de um .csv ou .xlsx."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise PlanilhaInvalida(f"{caminho}: arquivo nao encontrado")

    if caminho.suffix.lower() in (".xlsx", ".xlsm"):
        return _ler_xlsx(caminho)
    return _ler_csv(caminho)


def _ler_csv(caminho):
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        amostra = f.read(4096)
        f.seek(0)
        try:
            dialeto = csv.Sniffer().sniff(amostra, delimiters=",;\t")
        except csv.Error:
            dialeto = csv.excel  # arquivo de uma coluna so, por exemplo
        linhas = list(csv.reader(f, dialeto))

    if not linhas:
        raise PlanilhaInvalida(f"{caminho.name}: planilha vazia")
    return [normalizar(c) for c in linhas[0]], linhas[1:]


def _ler_xlsx(caminho):
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise PlanilhaInvalida(
            f"{caminho.name}: ler .xlsx precisa do openpyxl.\n"
            f"  Instale com 'pip install openpyxl' ou salve a planilha como .csv"
        ) from None

    planilha = load_workbook(caminho, read_only=True, data_only=True).active
    linhas = [
        ["" if c is None else str(c).strip() for c in linha]
        for linha in planilha.iter_rows(values_only=True)
    ]
    linhas = [l for l in linhas if any(l)]
    if not linhas:
        raise PlanilhaInvalida(f"{caminho.name}: planilha vazia")
    return [normalizar(c) for c in linhas[0]], linhas[1:]


def mapear_colunas(cabecalho, esperadas, caminho):
    """Liga cada campo que precisamos a sua posicao no cabecalho.

    `esperadas` e {campo: (lista de sinonimos, obrigatorio)}. Campo opcional
    ausente vira None e o chamador decide o que fazer.
    """
    posicoes = {}
    faltando = []
    for campo, (sinonimos, obrigatorio) in esperadas.items():
        indice = next(
            (cabecalho.index(s) for s in sinonimos if s in cabecalho), None)
        if indice is None and obrigatorio:
            faltando.append((campo, sinonimos))
        posicoes[campo] = indice

    if faltando:
        detalhe = "\n".join(
            f"  - {campo}: aceita {', '.join(repr(s) for s in sinonimos)}"
            for campo, sinonimos in faltando
        )
        raise PlanilhaInvalida(
            f"{Path(caminho).name}: faltam colunas obrigatorias.\n{detalhe}\n"
            f"  encontrado no arquivo: {', '.join(repr(c) for c in cabecalho if c)}"
        )
    return posicoes


def valor(linha, indice):
    """Celula como texto limpo; vazio quando a coluna nao existe ou acabou."""
    if indice is None or indice >= len(linha):
        return ""
    return str(linha[indice]).strip()
