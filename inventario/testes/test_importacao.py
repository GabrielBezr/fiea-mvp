"""Testes do modulo de importacao (RF06).

Rodar da pasta `inventario/`:  python -m unittest discover -s testes
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventario import esquema, leitor_csv  # noqa: E402
from inventario.importador import importar_arquivo, importar_caminho  # noqa: E402
from inventario.leitor_csv import ArquivoInvalido  # noqa: E402
from inventario.cadastro import importar_salas  # noqa: E402

CABECALHO = ",".join(leitor_csv.CABECALHO_LEITURAS)
META = "20261008090000,Casa da Industria,Gabriel,20261008090000-v01,,"\
       "estatica_porta,30,2026-10-08 09:10:00.000,2026-10-08 09:12:30.000,150"


def linha(timestamp, epc, rssi, referencia=""):
    meta = META.replace("-v01,,", f"-v01,{referencia},")
    return f"{meta},{timestamp},{epc},{rssi}"


def csv_varredura(linhas):
    return "\n".join([CABECALHO] + linhas) + "\n"


CSV_SESSAO = (
    "id_sessao,unidade,operador,inicio,fim,duracao_s,n_varreduras\n"
    "20261008090000,Casa da Industria,Gabriel,"
    "2026-10-08 09:00:00.000,2026-10-08 09:30:00.000,1800,1\n"
)


class BaseTemp(unittest.TestCase):
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


class TestDeduplicacao(BaseTemp):
    """O coracao do RF06."""

    def test_colapsa_repeticoes_da_mesma_etiqueta(self):
        caminho = self.escrever("leituras_20261008090000_v01.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80),
            linha("2026-10-08 09:10:02.000", "AAA", 90),
            linha("2026-10-08 09:10:03.000", "BBB", 70),
            linha("2026-10-08 09:10:04.000", "AAA", 85),
        ]))
        arquivo = leitor_csv.ler_varredura(caminho)

        self.assertEqual(arquivo.linhas_brutas, 4)
        self.assertEqual(arquivo.etiquetas_distintas, 2)
        self.assertEqual(arquivo.linhas_descartadas, 2)

    def test_preserva_a_evidencia_das_repeticoes(self):
        """Deduplicar nao e descartar: a estatistica de RSSI tem de sobreviver."""
        caminho = self.escrever("leituras_20261008090000_v01.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80),
            linha("2026-10-08 09:10:05.500", "AAA", 90),
            linha("2026-10-08 09:10:03.000", "AAA", 85),
        ]))
        leitura = leitor_csv.ler_varredura(caminho).leituras[0]

        self.assertEqual(leitura.leituras, 3)
        self.assertEqual(leitura.rssi_min, 80)
        self.assertEqual(leitura.rssi_max, 90)
        self.assertEqual(leitura.rssi_medio, 85.0)
        self.assertEqual(leitura.primeira_leitura, "2026-10-08 09:10:01.000")
        self.assertEqual(leitura.ultima_leitura, "2026-10-08 09:10:05.500")

    def test_mesma_etiqueta_em_varreduras_diferentes_nao_se_funde(self):
        """Escopo da dedup e a varredura; o RF08 depende disso."""
        self.escrever("v01.csv", csv_varredura([linha("2026-10-08 09:10:01.000", "AAA", 80)]))
        outra = csv_varredura([
            linha("2026-10-08 09:20:01.000", "AAA", 60).replace("-v01", "-v02")
        ])
        self.escrever("v02.csv", outra)

        importar_arquivo(self.pasta / "v01.csv", self.conexao)
        importar_arquivo(self.pasta / "v02.csv", self.conexao)

        total = self.conexao.execute(
            "SELECT COUNT(*) FROM leitura WHERE epc = 'AAA'").fetchone()[0]
        self.assertEqual(total, 2)


class TestIdempotencia(BaseTemp):
    def test_reimportar_nao_duplica(self):
        caminho = self.escrever("leituras_20261008090000_v01.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80),
            linha("2026-10-08 09:10:02.000", "BBB", 70),
        ]))
        importar_arquivo(caminho, self.conexao)
        resultado = importar_arquivo(caminho, self.conexao)

        self.assertTrue(resultado.reimportado)
        total = self.conexao.execute("SELECT COUNT(*) FROM leitura").fetchone()[0]
        self.assertEqual(total, 2)

    def test_reexportacao_corrigida_substitui_a_anterior(self):
        nome = "leituras_20261008090000_v01.csv"
        self.escrever(nome, csv_varredura([linha("2026-10-08 09:10:01.000", "AAA", 80)]))
        importar_arquivo(self.pasta / nome, self.conexao)

        self.escrever(nome, csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80),
            linha("2026-10-08 09:10:02.000", "CCC", 75),
        ]))
        importar_arquivo(self.pasta / nome, self.conexao)

        epcs = [r[0] for r in self.conexao.execute(
            "SELECT epc FROM leitura ORDER BY epc")]
        self.assertEqual(epcs, ["AAA", "CCC"])


class TestAtribuicaoSala(BaseTemp):
    def test_associa_varredura_a_sala_da_primeira_tag_de_referencia(self):
        salas = self.escrever(
            "salas.csv",
            "unidade,sala,posicao,epc\n"
            "Casa da Industria,Lab 1,centro,E28011REF01\n",
        )
        importar_salas(salas, self.conexao)
        caminho = self.escrever(
            "leituras_20261008090000_v01.csv",
            csv_varredura([linha(
                "2026-10-08 09:10:01.000", "E28011REF01", 80, "E28011REF01")]),
        )

        resultado = importar_arquivo(caminho, self.conexao)
        varredura = self.conexao.execute(
            "SELECT s.nome FROM varredura v "
            "JOIN sala s ON s.id = v.sala_id WHERE v.id = ?",
            (resultado.identificador,),
        ).fetchone()

        self.assertEqual(varredura["nome"], "Lab 1")
        self.assertEqual(resultado.sala, "Lab 1")

    def test_nao_associa_tag_de_outra_unidade(self):
        salas = self.escrever(
            "salas.csv",
            "unidade,sala,posicao,epc\nOutra unidade,Lab 1,centro,E28011REF01\n",
        )
        importar_salas(salas, self.conexao)
        caminho = self.escrever(
            "leituras_20261008090000_v01.csv",
            csv_varredura([linha(
                "2026-10-08 09:10:01.000", "E28011REF01", 80, "E28011REF01")]),
        )

        resultado = importar_arquivo(caminho, self.conexao)
        varredura = self.conexao.execute(
            "SELECT sala_id FROM varredura WHERE id = ?", (resultado.identificador,)
        ).fetchone()

        self.assertIsNone(varredura["sala_id"])
        self.assertEqual(resultado.sala, "")

    def test_recusa_epc_referencia_que_nao_foi_lido_no_arquivo(self):
        caminho = self.escrever(
            "leituras_20261008090000_v01.csv",
            csv_varredura([linha(
                "2026-10-08 09:10:01.000", "AAA", 80, "E28011REF01")]),
        )

        with self.assertRaisesRegex(ArquivoInvalido, "nao consta entre as leituras"):
            leitor_csv.ler_varredura(caminho)


class TestMigracaoEsquema(BaseTemp):
    def test_adiciona_sala_id_a_banco_existente(self):
        caminho_db = self.pasta / "banco_anterior.db"
        legado = sqlite3.connect(caminho_db)
        try:
            legado.executescript(
                "CREATE TABLE sessao (id TEXT PRIMARY KEY, unidade TEXT NOT NULL, "
                "operador TEXT NOT NULL, inicio TEXT, fim TEXT, duracao_s INTEGER, "
                "n_varreduras INTEGER);"
                "CREATE TABLE varredura (id TEXT PRIMARY KEY, sessao_id TEXT NOT NULL, "
                "numero INTEGER NOT NULL, epc_referencia TEXT, tecnica TEXT, "
                "potencia_dbm INTEGER, inicio TEXT NOT NULL, fim TEXT, "
                "duracao_s INTEGER, arquivo_origem TEXT NOT NULL, "
                "importado_em TEXT NOT NULL, linhas_brutas INTEGER NOT NULL);"
            )
        finally:
            legado.close()

        banco = esquema.conectar(caminho_db)
        self.addCleanup(banco.close)

        colunas = {
            coluna["name"] for coluna in banco.execute("PRAGMA table_info(varredura)")
        }
        self.assertIn("sala_id", colunas)


class TestOrdemDosArquivos(BaseTemp):
    """O aplicativo so exporta a sessao no fim, entao a varredura chega antes."""

    def test_varredura_antes_da_sessao_abre_registro_parcial(self):
        caminho = self.escrever("leituras_20261008090000_v01.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80)]))
        importar_arquivo(caminho, self.conexao)

        sessao = self.conexao.execute("SELECT * FROM sessao").fetchone()
        self.assertEqual(sessao["unidade"], "Casa da Industria")
        self.assertIsNone(sessao["inicio"])

        importar_arquivo(self.escrever("sessao_20261008090000.csv", CSV_SESSAO), self.conexao)
        sessao = self.conexao.execute("SELECT * FROM sessao").fetchone()
        self.assertEqual(sessao["duracao_s"], 1800)
        self.assertEqual(sessao["n_varreduras"], 1)

    def test_sessao_antes_da_varredura_tambem_funciona(self):
        importar_arquivo(self.escrever("sessao_20261008090000.csv", CSV_SESSAO), self.conexao)
        importar_arquivo(
            self.escrever("leituras_20261008090000_v01.csv", csv_varredura([
                linha("2026-10-08 09:10:01.000", "AAA", 80)])),
            self.conexao)

        sessao = self.conexao.execute("SELECT * FROM sessao").fetchone()
        self.assertEqual(sessao["duracao_s"], 1800)
        self.assertEqual(
            self.conexao.execute("SELECT COUNT(*) FROM varredura").fetchone()[0], 1)


class TestValidacao(BaseTemp):
    def test_recusa_cabecalho_diferente(self):
        caminho = self.escrever("x.csv", "epc,rssi,sala\nAAA,80,Lab\n")
        with self.assertRaises(ArquivoInvalido):
            leitor_csv.tipo_do_arquivo(caminho)

    def test_recusa_varredura_misturada_no_mesmo_arquivo(self):
        caminho = self.escrever("x.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80),
            linha("2026-10-08 09:10:02.000", "BBB", 70).replace("-v01", "-v02"),
        ]))
        with self.assertRaisesRegex(ArquivoInvalido, "id_varredura mudou"):
            leitor_csv.ler_varredura(caminho)

    def test_recusa_timestamp_fora_do_formato(self):
        caminho = self.escrever("x.csv", csv_varredura([linha("08/10/2026 09:10", "AAA", 80)]))
        with self.assertRaisesRegex(ArquivoInvalido, "timestamp"):
            leitor_csv.ler_varredura(caminho)

    def test_recusa_rssi_nao_inteiro(self):
        caminho = self.escrever("x.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", "forte")]))
        with self.assertRaisesRegex(ArquivoInvalido, "rssi"):
            leitor_csv.ler_varredura(caminho)

    def test_recusa_arquivo_sem_leituras(self):
        caminho = self.escrever("x.csv", CABECALHO + "\n")
        with self.assertRaisesRegex(ArquivoInvalido, "sem nenhuma leitura"):
            leitor_csv.ler_varredura(caminho)

    def test_aponta_a_linha_do_erro(self):
        caminho = self.escrever("x.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80),
            linha("2026-10-08 09:10:02.000", "BBB", 70),
            linha("2026-10-08 09:10:03.000", "CCC", "x"),
        ]))
        with self.assertRaisesRegex(ArquivoInvalido, r"x\.csv:4"):
            leitor_csv.ler_varredura(caminho)


class TestArquivamento(BaseTemp):
    def test_preserva_o_csv_bruto(self):
        """RNF05: o arquivo original do leitor nao pode se perder."""
        caminho = self.escrever("leituras_20261008090000_v01.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80)]))
        brutos = self.pasta / "brutos"
        importar_arquivo(caminho, self.conexao, pasta_brutos=brutos)

        copia = brutos / caminho.name
        self.assertTrue(copia.is_file())
        self.assertEqual(copia.read_text(encoding="utf-8"),
                         caminho.read_text(encoding="utf-8"))


class TestPasta(BaseTemp):
    def test_importa_a_pasta_inteira(self):
        self.escrever("leituras_20261008090000_v01.csv", csv_varredura([
            linha("2026-10-08 09:10:01.000", "AAA", 80)]))
        self.escrever("sessao_20261008090000.csv", CSV_SESSAO)
        self.escrever("anotacoes.txt", "nao e csv")

        resultados = importar_caminho(self.pasta, self.conexao)
        tipos = sorted(r.tipo for r in resultados)
        self.assertEqual(tipos, ["sessao", "varredura"])


if __name__ == "__main__":
    unittest.main()
