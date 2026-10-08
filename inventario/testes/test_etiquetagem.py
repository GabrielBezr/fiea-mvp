"""Testes da etiquetagem (RF03): vinculo entre EPC e numero de patrimonio."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventario import esquema  # noqa: E402
from inventario.cadastro import importar_bens, importar_salas  # noqa: E402
from inventario.etiquetagem import (  # noqa: E402
    EtiquetagemInvalida, bens_sem_etiqueta, importar_etiquetagem, progresso,
)

SALAS = """unidade,sala,posicao,epc
Casa da Industria,Lab 1,centro,E2801160REF0100
"""

BENS = """patrimonio,descricao,sala esperada
10001,Microcomputador,Lab 1
10002,Monitor,Lab 1
10003,Armario de aco,Lab 1
"""

CABECALHO = "timestamp,epc,patrimonio,metalico,origem_epc"


def csv_etiquetagem(linhas):
    return "\n".join([CABECALHO] + linhas) + "\n"


def par(epc, patrimonio, metalico="nao", origem="rfid", quando="2026-10-08 09:00:00.000"):
    return f"{quando},{epc},{patrimonio},{metalico},{origem}"


class Base(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.pasta = Path(self._temp.name)
        self.conexao = esquema.conectar(self.pasta / "teste.db")
        self.addCleanup(self._temp.cleanup)
        self.addCleanup(self.conexao.close)
        importar_salas(self.escrever("salas.csv", SALAS), self.conexao)
        importar_bens(self.escrever("bens.csv", BENS), self.conexao)

    def escrever(self, nome, conteudo):
        caminho = self.pasta / nome
        caminho.write_text(conteudo, encoding="utf-8")
        return caminho

    def epc_de(self, patrimonio):
        return self.conexao.execute(
            "SELECT epc FROM bem WHERE patrimonio = ?", (patrimonio,)).fetchone()["epc"]


class TestVinculo(Base):
    def test_vincula_etiqueta_ao_bem(self):
        caminho = self.escrever("etiq.csv", csv_etiquetagem([
            par("E2800BEM0001", "10001"),
            par("E2800BEM0002", "10002"),
        ]))
        relatorio = importar_etiquetagem(caminho, self.conexao)

        self.assertEqual(relatorio.vinculados, 2)
        self.assertEqual(relatorio.inconsistencias, [])
        self.assertEqual(self.epc_de("10001"), "E2800BEM0001")

    def test_conta_progresso_da_campanha(self):
        self.escrever("etiq.csv", csv_etiquetagem([par("E2800BEM0001", "10001")]))
        importar_etiquetagem(self.pasta / "etiq.csv", self.conexao)

        self.assertEqual(progresso(self.conexao), (1, 3))
        faltam = [b["patrimonio"] for b in bens_sem_etiqueta(self.conexao)]
        self.assertEqual(faltam, ["10002", "10003"])

    def test_normaliza_epc_minusculo(self):
        caminho = self.escrever("etiq.csv", csv_etiquetagem([
            par("e2800bem0001", "10001")]))
        importar_etiquetagem(caminho, self.conexao)

        self.assertEqual(self.epc_de("10001"), "E2800BEM0001")

    def test_marca_metalico_observado_em_campo(self):
        caminho = self.escrever("etiq.csv", csv_etiquetagem([
            par("E2800BEM0003", "10003", metalico="sim")]))
        importar_etiquetagem(caminho, self.conexao)

        metalico = self.conexao.execute(
            "SELECT metalico FROM bem WHERE patrimonio = '10003'").fetchone()[0]
        self.assertEqual(metalico, 1)

    def test_reimportar_o_mesmo_arquivo_nao_muda_nada(self):
        self.escrever("etiq.csv", csv_etiquetagem([par("E2800BEM0001", "10001")]))
        importar_etiquetagem(self.pasta / "etiq.csv", self.conexao)
        importar_etiquetagem(self.pasta / "etiq.csv", self.conexao)

        self.assertEqual(progresso(self.conexao), (1, 3))
        self.assertEqual(self.epc_de("10001"), "E2800BEM0001")


class TestOrigemDoEpc(Base):
    def test_separa_os_capturados_so_pelo_qr(self):
        """Etiqueta que nao respondeu ao radio na bancada e alerta de campo."""
        caminho = self.escrever("etiq.csv", csv_etiquetagem([
            par("E2800BEM0001", "10001", origem="rfid"),
            par("E2800BEM0003", "10003", metalico="sim", origem="manual"),
        ]))
        relatorio = importar_etiquetagem(caminho, self.conexao)

        self.assertEqual(relatorio.vinculados, 2)
        self.assertEqual(relatorio.so_por_qr, 1)


class TestRecusas(Base):
    def test_recusa_patrimonio_fora_da_relacao(self):
        caminho = self.escrever("etiq.csv", csv_etiquetagem([
            par("E2800BEM9999", "99999")]))
        relatorio = importar_etiquetagem(caminho, self.conexao)

        self.assertEqual(relatorio.aceitos, 0)
        self.assertIn("nao esta na relacao patrimonial", relatorio.inconsistencias[0][1])

    def test_recusa_a_mesma_etiqueta_em_dois_bens(self):
        """Aceitar deixaria um dos dois como nao localizado para sempre."""
        self.escrever("etiq.csv", csv_etiquetagem([par("E2800BEM0001", "10001")]))
        importar_etiquetagem(self.pasta / "etiq.csv", self.conexao)

        caminho = self.escrever("outro.csv", csv_etiquetagem([
            par("E2800BEM0001", "10002")]))
        relatorio = importar_etiquetagem(caminho, self.conexao)

        self.assertEqual(relatorio.aceitos, 0)
        self.assertIn("ja esta no patrimonio 10001", relatorio.inconsistencias[0][1])
        self.assertIsNone(self.epc_de("10002"))

    def test_recusa_epc_repetido_no_mesmo_arquivo(self):
        caminho = self.escrever("etiq.csv", csv_etiquetagem([
            par("E2800BEM0001", "10001"),
            par("E2800BEM0001", "10002"),
        ]))
        relatorio = importar_etiquetagem(caminho, self.conexao)

        self.assertEqual(relatorio.vinculados, 1)
        self.assertIn("repetido", relatorio.inconsistencias[0][1])

    def test_troca_de_etiqueta_e_aplicada_mas_fica_registrada(self):
        """Etiqueta danificada e substituida; o registro evita troca silenciosa."""
        self.escrever("etiq.csv", csv_etiquetagem([par("E2800BEM0001", "10001")]))
        importar_etiquetagem(self.pasta / "etiq.csv", self.conexao)

        caminho = self.escrever("nova.csv", csv_etiquetagem([
            par("E2800BEMNOVA", "10001")]))
        relatorio = importar_etiquetagem(caminho, self.conexao)

        self.assertEqual(relatorio.revinculados, 1)
        self.assertIn("substituida", relatorio.inconsistencias[0][1])
        self.assertEqual(self.epc_de("10001"), "E2800BEMNOVA")

    def test_recusa_linha_incompleta_sem_derrubar_o_arquivo(self):
        caminho = self.escrever("etiq.csv", csv_etiquetagem([
            par("E2800BEM0001", "10001"),
            par("", "10002"),
            par("E2800BEM0003", "10003"),
        ]))
        relatorio = importar_etiquetagem(caminho, self.conexao)

        self.assertEqual(relatorio.vinculados, 2)
        self.assertEqual(len(relatorio.inconsistencias), 1)

    def test_recusa_cabecalho_diferente(self):
        caminho = self.escrever("etiq.csv", "epc,patrimonio\nAAA,10001\n")
        with self.assertRaisesRegex(EtiquetagemInvalida, "cabecalho inesperado"):
            importar_etiquetagem(caminho, self.conexao)


if __name__ == "__main__":
    unittest.main()
