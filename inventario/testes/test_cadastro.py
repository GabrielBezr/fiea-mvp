"""Testes do cadastro do administrador: RF01 (salas e tags) e RF02 (bens)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventario import esquema  # noqa: E402
from inventario.cadastro import (  # noqa: E402
    bens_sem_sala, importar_bens, importar_salas, salas_com_poucas_tags,
)
from inventario.planilha import PlanilhaInvalida, normalizar  # noqa: E402

SALAS_OK = """unidade,sala,posicao,epc
Casa da Industria,Lab 1,centro,E2801160REF0101
Casa da Industria,Lab 1,canto_ne,E2801160REF0102
Casa da Industria,Lab 1,canto_nw,E2801160REF0103
Casa da Industria,Lab 1,canto_se,E2801160REF0104
Casa da Industria,Lab 1,canto_sw,E2801160REF0105
Casa da Industria,Sala TI,centro,E2801160REF0201
"""


class Base(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.pasta = Path(self._temp.name)
        self.conexao = esquema.conectar(self.pasta / "teste.db")
        self.addCleanup(self._temp.cleanup)
        self.addCleanup(self.conexao.close)

    def escrever(self, nome, conteudo):
        caminho = self.pasta / nome
        caminho.write_text(conteudo, encoding="utf-8")
        return caminho

    def com_salas(self):
        importar_salas(self.escrever("salas.csv", SALAS_OK), self.conexao)


class TestSalas(Base):
    def test_cria_salas_e_tags(self):
        relatorio = importar_salas(self.escrever("salas.csv", SALAS_OK), self.conexao)

        self.assertEqual(relatorio.criados, 6)
        self.assertEqual(relatorio.inconsistencias, [])
        self.assertEqual(
            self.conexao.execute("SELECT COUNT(*) FROM sala").fetchone()[0], 2)
        self.assertEqual(
            self.conexao.execute("SELECT COUNT(*) FROM tag_referencia").fetchone()[0], 6)

    def test_reimportar_nao_duplica(self):
        self.com_salas()
        relatorio = importar_salas(self.pasta / "salas.csv", self.conexao)

        self.assertEqual(relatorio.criados, 0)
        self.assertEqual(relatorio.atualizados, 6)
        self.assertEqual(
            self.conexao.execute("SELECT COUNT(*) FROM tag_referencia").fetchone()[0], 6)

    def test_recusa_a_mesma_tag_em_duas_salas(self):
        """Aceitar mandaria a varredura inteira para o ambiente errado."""
        self.com_salas()
        caminho = self.escrever("mover.csv",
                                "unidade,sala,posicao,epc\n"
                                "Casa da Industria,Sala TI,canto_ne,E2801160REF0101\n")
        relatorio = importar_salas(caminho, self.conexao)

        self.assertEqual(relatorio.aceitos, 0)
        self.assertIn("ja cadastrado em outra sala", relatorio.inconsistencias[0][1])

    def test_recusa_epc_repetido_no_mesmo_arquivo(self):
        caminho = self.escrever("salas.csv",
                                "unidade,sala,posicao,epc\n"
                                "U,A,centro,AAA\n"
                                "U,B,centro,AAA\n")
        relatorio = importar_salas(caminho, self.conexao)

        self.assertEqual(relatorio.criados, 1)
        self.assertIn("repetido", relatorio.inconsistencias[0][1])

    def test_recusa_duas_tags_na_mesma_posicao(self):
        self.com_salas()
        caminho = self.escrever("x.csv",
                                "unidade,sala,posicao,epc\n"
                                "Casa da Industria,Lab 1,centro,E2801160NOVA\n")
        relatorio = importar_salas(caminho, self.conexao)

        self.assertEqual(relatorio.aceitos, 0)
        self.assertIn("ja tem a tag", relatorio.inconsistencias[0][1])

    def test_avisa_sala_com_poucas_tags(self):
        self.com_salas()
        faltando = salas_com_poucas_tags(self.conexao, minimo=5)

        self.assertEqual([s["nome"] for s in faltando], ["Sala TI"])

    def test_linha_incompleta_vira_inconsistencia_sem_derrubar_o_arquivo(self):
        caminho = self.escrever("salas.csv",
                                "unidade,sala,posicao,epc\n"
                                "U,A,centro,AAA\n"
                                ",,,\n"
                                "U,B,,BBB\n"
                                "U,C,centro,CCC\n")
        relatorio = importar_salas(caminho, self.conexao)

        self.assertEqual(relatorio.criados, 2)
        self.assertEqual(len(relatorio.inconsistencias), 1)


class TestBens(Base):
    def test_importa_a_relacao_patrimonial(self):
        self.com_salas()
        caminho = self.escrever("bens.csv",
                                "patrimonio,descricao,sala esperada\n"
                                "1001,Microcomputador,Lab 1\n"
                                "1002,Monitor 24,Lab 1\n"
                                "1003,Switch,Sala TI\n")
        relatorio = importar_bens(caminho, self.conexao)

        self.assertEqual(relatorio.criados, 3)
        self.assertEqual(relatorio.inconsistencias, [])
        self.assertEqual(bens_sem_sala(self.conexao), 0)

    def test_aceita_cabecalho_da_fiea_com_acento_e_maiuscula(self):
        """A planilha vem do cliente, nao do nosso aplicativo (risco da secao 5.1)."""
        self.com_salas()
        caminho = self.escrever("bens.csv",
                                "N.º Patrimônio;DESCRIÇÃO DO BEM;Localização\n"
                                "1001;Microcomputador;Lab 1\n")
        relatorio = importar_bens(caminho, self.conexao)

        self.assertEqual(relatorio.criados, 1)
        bem = self.conexao.execute("SELECT * FROM bem").fetchone()
        self.assertEqual(bem["descricao"], "Microcomputador")
        self.assertIsNotNone(bem["sala_esperada_id"])

    def test_bem_de_sala_desconhecida_entra_mas_e_apontado(self):
        self.com_salas()
        caminho = self.escrever("bens.csv",
                                "patrimonio,descricao,sala esperada\n"
                                "1001,Microcomputador,Almoxarifado\n")
        relatorio = importar_bens(caminho, self.conexao)

        self.assertEqual(relatorio.criados, 1)
        self.assertIn("nao esta cadastrada", relatorio.inconsistencias[0][1])
        self.assertEqual(bens_sem_sala(self.conexao), 1)

    def test_cadastrar_a_sala_depois_conserta_na_reimportacao(self):
        self.com_salas()
        self.escrever("bens.csv",
                      "patrimonio,descricao,sala esperada\n"
                      "1001,Microcomputador,Almoxarifado\n")
        importar_bens(self.pasta / "bens.csv", self.conexao)

        importar_salas(
            self.escrever("mais.csv",
                          "unidade,sala,posicao,epc\n"
                          "Casa da Industria,Almoxarifado,centro,E2801160REF0301\n"),
            self.conexao)
        importar_bens(self.pasta / "bens.csv", self.conexao)

        self.assertEqual(bens_sem_sala(self.conexao), 0)

    def test_reimportar_preserva_a_etiquetagem(self):
        """o epc vem da campanha de etiquetagem; a relacao nao pode apaga-lo."""
        self.com_salas()
        self.escrever("bens.csv",
                      "patrimonio,descricao,sala esperada\n"
                      "1001,Microcomputador,Lab 1\n")
        importar_bens(self.pasta / "bens.csv", self.conexao)
        self.conexao.execute(
            "UPDATE bem SET epc = 'E2800BEM1001' "
            "WHERE patrimonio = '1001'")
        self.conexao.commit()

        self.escrever("bens.csv",
                      "patrimonio,descricao,sala esperada\n"
                      "1001,Microcomputador Dell,Lab 1\n")
        importar_bens(self.pasta / "bens.csv", self.conexao)

        bem = self.conexao.execute("SELECT * FROM bem").fetchone()
        self.assertEqual(bem["descricao"], "Microcomputador Dell")
        self.assertEqual(bem["epc"], "E2800BEM1001")

    def test_patrimonio_repetido_entra_uma_vez_so(self):
        self.com_salas()
        caminho = self.escrever("bens.csv",
                                "patrimonio,descricao,sala esperada\n"
                                "1001,Microcomputador,Lab 1\n"
                                "1001,Microcomputador,Sala TI\n")
        relatorio = importar_bens(caminho, self.conexao)

        self.assertEqual(relatorio.criados, 1)
        self.assertIn("repetido", relatorio.inconsistencias[0][1])

    def test_marca_bens_metalicos(self):
        self.com_salas()
        caminho = self.escrever("bens.csv",
                                "patrimonio,descricao,sala esperada,metalico\n"
                                "1001,Armario de aco,Lab 1,sim\n"
                                "1002,Cadeira,Lab 1,nao\n")
        importar_bens(caminho, self.conexao)

        metalicos = self.conexao.execute(
            "SELECT patrimonio FROM bem WHERE metalico = 1").fetchall()
        self.assertEqual([m["patrimonio"] for m in metalicos], ["1001"])

    def test_exige_unidade_quando_ha_mais_de_uma(self):
        importar_salas(
            self.escrever("salas.csv",
                          "unidade,sala,posicao,epc\n"
                          "Casa da Industria,Lab 1,centro,AAA\n"
                          "SENAI Benedito Bentes,Lab 1,centro,BBB\n"),
            self.conexao)
        relatorio = importar_bens(
            self.escrever("bens.csv",
                          "patrimonio,descricao,sala esperada\n"
                          "1001,Microcomputador,Lab 1\n"),
            self.conexao)

        self.assertIn("mais de uma unidade", relatorio.inconsistencias[0][1])

    def test_recusa_planilha_sem_coluna_obrigatoria(self):
        self.com_salas()
        caminho = self.escrever("bens.csv", "codigo,nome\n1,Mesa\n")
        with self.assertRaisesRegex(PlanilhaInvalida, "faltam colunas"):
            importar_bens(caminho, self.conexao)


class TestNormalizacao(unittest.TestCase):
    def test_normaliza_cabecalhos_do_mundo_real(self):
        self.assertEqual(normalizar("N.º  Patrimônio "), "n patrimonio")
        self.assertEqual(normalizar("DESCRIÇÃO DO BEM"), "descricao do bem")
        self.assertEqual(normalizar("Sala_Esperada"), "sala esperada")
        self.assertEqual(normalizar(None), "")


if __name__ == "__main__":
    unittest.main()
