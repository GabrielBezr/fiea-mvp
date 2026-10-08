"""Importacao da etiquetagem (RF03): liga o EPC lido ao numero de patrimonio.

Sem esta ligacao a conciliacao e impossivel, porque uma leitura e so um EPC sem
significado. O arquivo vem da tela de etiquetagem do DT50:

    timestamp,epc,patrimonio,metalico,origem_epc

`origem_epc` diz se o EPC veio do radio ou do QR impresso na etiqueta. Etiqueta
capturada so pelo QR e etiqueta que nao respondeu ao radio na bancada, e que
provavelmente tambem nao vai responder na varredura -- por isso o relatorio
separa essa contagem em vez de engoli-la.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

CABECALHO = ["timestamp", "epc", "patrimonio", "metalico", "origem_epc"]
VERDADEIROS = {"1", "s", "sim", "true", "v", "verdadeiro", "x", "y", "yes"}
ORIGEM_RFID = "rfid"


class EtiquetagemInvalida(Exception):
    """Arquivo de etiquetagem fora do formato esperado."""


@dataclass
class RelatorioEtiquetagem:
    arquivo: str
    vinculados: int = 0
    revinculados: int = 0
    so_por_qr: int = 0
    inconsistencias: list = field(default_factory=list)

    @property
    def aceitos(self):
        return self.vinculados + self.revinculados

    def anotar(self, numero_linha, problema):
        self.inconsistencias.append((numero_linha, problema))


def importar_etiquetagem(caminho, conexao):
    """Aplica os pares a tabela `bem`, uma linha ruim de cada vez."""
    caminho = Path(caminho)
    if not caminho.is_file():
        raise EtiquetagemInvalida(f"{caminho}: arquivo nao encontrado")

    with caminho.open(encoding="utf-8-sig", newline="") as f:
        leitor = csv.DictReader(f)
        if list(leitor.fieldnames or []) != CABECALHO:
            raise EtiquetagemInvalida(
                f"{caminho.name}: cabecalho inesperado.\n"
                f"  esperado: {','.join(CABECALHO)}\n"
                f"  encontrado: {','.join(leitor.fieldnames or [])}"
            )
        linhas = list(leitor)

    relatorio = RelatorioEtiquetagem(arquivo=str(caminho))
    vistos = {}

    for numero_linha, linha in enumerate(linhas, start=2):
        epc = (linha.get("epc") or "").strip().upper()
        patrimonio = (linha.get("patrimonio") or "").strip()
        if not epc and not patrimonio:
            continue
        if not epc or not patrimonio:
            relatorio.anotar(numero_linha, "epc e patrimonio sao obrigatorios")
            continue
        if epc in vistos:
            relatorio.anotar(
                numero_linha, f"epc {epc} repetido (ja na linha {vistos[epc]})")
            continue
        vistos[epc] = numero_linha

        bem = conexao.execute(
            "SELECT patrimonio, epc FROM bem WHERE patrimonio = ?", (patrimonio,)
        ).fetchone()
        if bem is None:
            # Sem o bem na relacao patrimonial nao ha o que vincular. Pode ser
            # digito errado no patrimonio ou relacao desatualizada (secao 5.1).
            relatorio.anotar(
                numero_linha,
                f"patrimonio {patrimonio} nao esta na relacao patrimonial (RF02)")
            continue

        dono = conexao.execute(
            "SELECT patrimonio FROM bem WHERE epc = ? AND patrimonio <> ?",
            (epc, patrimonio),
        ).fetchone()
        if dono is not None:
            # Uma etiqueta num bem so: aceitar faria dois bens disputarem a mesma
            # leitura e um deles apareceria como nao localizado para sempre.
            relatorio.anotar(
                numero_linha,
                f"etiqueta {epc} ja esta no patrimonio {dono['patrimonio']}")
            continue

        trocou = bem["epc"] is not None and bem["epc"] != epc
        if trocou:
            relatorio.anotar(
                numero_linha,
                f"patrimonio {patrimonio} tinha a etiqueta {bem['epc']}; "
                f"substituida por {epc}")

        metalico = _booleano(linha.get("metalico"))
        conexao.execute(
            "UPDATE bem SET epc = ?, metalico = COALESCE(?, metalico) "
            "WHERE patrimonio = ?",
            (epc, metalico, patrimonio),
        )

        if bem["epc"] is None:
            relatorio.vinculados += 1
        else:
            relatorio.revinculados += 1
        if (linha.get("origem_epc") or "").strip().lower() != ORIGEM_RFID:
            relatorio.so_por_qr += 1

    conexao.commit()
    return relatorio


def progresso(conexao):
    """Quanto da relacao patrimonial ja esta etiquetada."""
    total = conexao.execute("SELECT COUNT(*) FROM bem").fetchone()[0]
    com_epc = conexao.execute(
        "SELECT COUNT(*) FROM bem WHERE epc IS NOT NULL").fetchone()[0]
    return com_epc, total


def bens_sem_etiqueta(conexao, limite=10):
    """Bens que a varredura nunca vai conseguir identificar."""
    return conexao.execute(
        "SELECT b.patrimonio, b.descricao, s.nome AS sala "
        "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
        "WHERE b.epc IS NULL ORDER BY b.patrimonio LIMIT ?",
        (limite,),
    ).fetchall()


def _booleano(texto):
    if not texto or not texto.strip():
        return None
    return 1 if texto.strip().lower() in VERDADEIROS else 0
