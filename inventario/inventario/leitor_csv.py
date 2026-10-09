"""Leitura e validacao dos CSV exportados pelo leitor DT50.

O aplicativo exporta dois tipos de arquivo:
  leituras_<id_sessao>_v<nn>.csv  uma linha por evento de leitura
  sessao_<id_sessao>.csv          uma linha, com o tempo total da sessao

Aqui acontece a deduplicacao do RF06: as varias linhas da mesma etiqueta dentro
da mesma varredura viram uma unica LeituraAgregada. O escopo e a varredura, nao
a sessao -- a mesma etiqueta lida em duas salas continua sendo dois registros,
que e do que o RF08 precisa para detectar leitura cruzada.
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FORMATO_INSTANTE = "%Y-%m-%d %H:%M:%S.%f"

CABECALHO_LEITURAS = [
    "id_sessao", "unidade", "operador", "id_varredura", "epc_referencia",
    "tecnica", "potencia_dbm", "varredura_inicio", "varredura_fim",
    "varredura_duracao_s", "timestamp", "epc", "rssi",
]

CABECALHO_SESSAO = [
    "id_sessao", "unidade", "operador", "inicio", "fim", "duracao_s", "n_varreduras",
]


class ArquivoInvalido(Exception):
    """CSV fora do formato esperado. A mensagem aponta a linha."""


@dataclass
class LeituraAgregada:
    """Uma etiqueta vista numa varredura, com a evidencia das repeticoes.

    A contagem e as estatisticas de RSSI sao justamente o que a conciliacao
    (RF07/RF08) vai usar; por isso deduplicar aqui nunca e simplesmente jogar
    linhas fora.
    """
    epc: str
    leituras: int
    rssi_min: int
    rssi_medio: float
    rssi_max: int
    primeira_leitura: str
    ultima_leitura: str


@dataclass
class ArquivoVarredura:
    sessao_id: str
    unidade: str
    operador: str
    varredura_id: str
    numero: int
    epc_referencia: str
    tecnica: str
    potencia_dbm: int
    inicio: str
    fim: str
    duracao_s: int
    linhas_brutas: int
    leituras: list = field(default_factory=list)

    @property
    def etiquetas_distintas(self):
        return len(self.leituras)

    @property
    def linhas_descartadas(self):
        """Quantas linhas o RF06 colapsou."""
        return self.linhas_brutas - self.etiquetas_distintas


@dataclass
class ArquivoSessao:
    sessao_id: str
    unidade: str
    operador: str
    inicio: str
    fim: str
    duracao_s: int
    n_varreduras: int


def tipo_do_arquivo(caminho):
    """Descobre o tipo pelo cabecalho, nao pelo nome do arquivo."""
    cabecalho = _ler_cabecalho(caminho)
    if cabecalho == CABECALHO_LEITURAS:
        return "varredura"
    if cabecalho == CABECALHO_SESSAO:
        return "sessao"
    raise ArquivoInvalido(
        f"{Path(caminho).name}: cabecalho nao reconhecido.\n"
        f"  encontrado: {','.join(cabecalho)}"
    )


def ler_varredura(caminho):
    """Le um arquivo de leituras e devolve a varredura ja deduplicada."""
    caminho = Path(caminho)
    agregados = {}
    linhas = 0
    metadados = None

    with caminho.open(encoding="utf-8-sig", newline="") as f:
        leitor = csv.DictReader(f)
        _exigir_cabecalho(leitor.fieldnames, CABECALHO_LEITURAS, caminho)

        for numero_linha, linha in enumerate(leitor, start=2):
            _exigir_campos_completos(linha, caminho, numero_linha)
            if metadados is None:
                metadados = _metadados(linha, caminho, numero_linha)
            elif linha["id_varredura"].strip() != metadados["varredura_id"]:
                # Um arquivo e sempre uma varredura so; misturar quebraria o
                # escopo da deduplicacao.
                raise ArquivoInvalido(
                    f"{caminho.name}:{numero_linha}: id_varredura mudou no meio do arquivo "
                    f"({metadados['varredura_id']} -> {linha['id_varredura'].strip()})"
                )

            epc = linha["epc"].strip().upper()
            if not epc:
                raise ArquivoInvalido(f"{caminho.name}:{numero_linha}: epc vazio")
            instante = _exigir_instante(linha["timestamp"], caminho, numero_linha, "timestamp")
            rssi = _exigir_inteiro(linha["rssi"], caminho, numero_linha, "rssi")

            linhas += 1
            acumulado = agregados.get(epc)
            if acumulado is None:
                agregados[epc] = {
                    "leituras": 1, "soma": rssi, "min": rssi, "max": rssi,
                    "primeira": instante, "ultima": instante,
                }
            else:
                acumulado["leituras"] += 1
                acumulado["soma"] += rssi
                acumulado["min"] = min(acumulado["min"], rssi)
                acumulado["max"] = max(acumulado["max"], rssi)
                acumulado["primeira"] = min(acumulado["primeira"], instante)
                acumulado["ultima"] = max(acumulado["ultima"], instante)

    if metadados is None:
        raise ArquivoInvalido(f"{caminho.name}: arquivo sem nenhuma leitura")
    if metadados["epc_referencia"] and metadados["epc_referencia"] not in agregados:
        raise ArquivoInvalido(
            f"{caminho.name}: tag de referencia {metadados['epc_referencia']} "
            "nao consta entre as leituras da varredura"
        )

    arquivo = ArquivoVarredura(linhas_brutas=linhas, **metadados)
    arquivo.leituras = [
        LeituraAgregada(
            epc=epc,
            leituras=a["leituras"],
            rssi_min=a["min"],
            rssi_medio=round(a["soma"] / a["leituras"], 2),
            rssi_max=a["max"],
            primeira_leitura=_formatar(a["primeira"]),
            ultima_leitura=_formatar(a["ultima"]),
        )
        for epc, a in sorted(agregados.items())
    ]
    return arquivo


def ler_sessao(caminho):
    """Le o arquivo de sessao, que fecha o tempo total (RF04/RF11)."""
    caminho = Path(caminho)
    with caminho.open(encoding="utf-8-sig", newline="") as f:
        leitor = csv.DictReader(f)
        _exigir_cabecalho(leitor.fieldnames, CABECALHO_SESSAO, caminho)
        linhas = list(leitor)

    if len(linhas) != 1:
        raise ArquivoInvalido(
            f"{caminho.name}: esperada 1 linha de sessao, encontradas {len(linhas)}"
        )
    linha = linhas[0]
    _exigir_campos_completos(linha, caminho, 2)
    return ArquivoSessao(
        sessao_id=linha["id_sessao"].strip(),
        unidade=linha["unidade"].strip(),
        operador=linha["operador"].strip(),
        inicio=_texto_instante(linha["inicio"], caminho, 2, "inicio"),
        fim=_texto_instante(linha["fim"], caminho, 2, "fim"),
        duracao_s=_inteiro_opcional(linha["duracao_s"], caminho, 2, "duracao_s"),
        n_varreduras=_inteiro_opcional(linha["n_varreduras"], caminho, 2, "n_varreduras"),
    )


# ---------------------------------------------------------------- auxiliares

def _formatar(instante):
    """Volta ao texto do aplicativo: milissegundos, nao microssegundos."""
    return instante.strftime(FORMATO_INSTANTE)[:-3]


def _ler_cabecalho(caminho):
    with Path(caminho).open(encoding="utf-8-sig", newline="") as f:
        primeira = f.readline().strip()
    return [c.strip() for c in primeira.split(",")] if primeira else []


def _exigir_cabecalho(encontrado, esperado, caminho):
    if list(encontrado or []) != esperado:
        raise ArquivoInvalido(
            f"{Path(caminho).name}: cabecalho inesperado.\n"
            f"  esperado: {','.join(esperado)}\n"
            f"  encontrado: {','.join(encontrado or [])}"
        )


def _exigir_campos_completos(linha, caminho, numero_linha):
    if None in linha.values() or None in linha:
        raise ArquivoInvalido(
            f"{Path(caminho).name}:{numero_linha}: numero de colunas diferente do cabecalho"
        )


def _metadados(linha, caminho, numero_linha):
    varredura_id = linha["id_varredura"].strip()
    return {
        "sessao_id": linha["id_sessao"].strip(),
        "unidade": linha["unidade"].strip(),
        "operador": linha["operador"].strip(),
        "varredura_id": varredura_id,
        "numero": _numero_da_varredura(varredura_id, caminho, numero_linha),
        "epc_referencia": linha["epc_referencia"].strip().upper(),
        "tecnica": linha["tecnica"].strip(),
        "potencia_dbm": _exigir_inteiro(
            linha["potencia_dbm"], caminho, numero_linha, "potencia_dbm"),
        "inicio": _texto_instante(
            linha["varredura_inicio"], caminho, numero_linha, "varredura_inicio"),
        "fim": _texto_instante(
            linha["varredura_fim"], caminho, numero_linha, "varredura_fim"),
        "duracao_s": _inteiro_opcional(
            linha["varredura_duracao_s"], caminho, numero_linha, "varredura_duracao_s"),
    }


def _numero_da_varredura(varredura_id, caminho, numero_linha):
    """O numero vem do sufixo -v<nn> que o aplicativo monta."""
    sufixo = varredura_id.rsplit("-v", 1)[-1] if "-v" in varredura_id else ""
    if not sufixo.isdigit():
        raise ArquivoInvalido(
            f"{Path(caminho).name}:{numero_linha}: id_varredura '{varredura_id}' "
            f"nao segue o padrao <id_sessao>-v<nn>"
        )
    return int(sufixo)


def _exigir_instante(texto, caminho, numero_linha, campo):
    try:
        return datetime.strptime(texto.strip(), FORMATO_INSTANTE)
    except ValueError:
        raise ArquivoInvalido(
            f"{Path(caminho).name}:{numero_linha}: {campo} '{texto}' nao esta no "
            f"formato aaaa-mm-dd hh:mm:ss.mmm"
        ) from None


def _texto_instante(texto, caminho, numero_linha, campo):
    """Mantem o texto original, ja ordenavel, mas valida o formato."""
    texto = texto.strip()
    if not texto:
        return None
    _exigir_instante(texto, caminho, numero_linha, campo)
    return texto


def _exigir_inteiro(texto, caminho, numero_linha, campo):
    try:
        return int(texto.strip())
    except ValueError:
        raise ArquivoInvalido(
            f"{Path(caminho).name}:{numero_linha}: {campo} '{texto}' nao e inteiro"
        ) from None


def _inteiro_opcional(texto, caminho, numero_linha, campo):
    texto = texto.strip()
    return _exigir_inteiro(texto, caminho, numero_linha, campo) if texto else None
