"""Testes da conciliacao de bens com as leituras de uma sessao."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventario import esquema  # noqa: E402
from inventario.conferencia import (  # noqa: E402
    FORA_DO_LOCAL, LOCALIZADO, NAO_CADASTRADO, NAO_LOCALIZADO, SEM_COBERTURA,
    SessaoNaoEncontrada, conferir_sessao,
)
from inventario.cadastro import importar_salas  # noqa: E402


class TestConferencia(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.conexao = esquema.conectar(Path(self._temp.name) / "teste.db")
        self.addCleanup(self.conexao.close)

        salas_csv = Path(self._temp.name) / "salas.csv"
        salas_csv.write_text(
            "unidade,sala,posicao,epc\n"
            "Unidade 1,Lab A,centro,REF_A\n"
            "Unidade 1,Lab B,centro,REF_B\n"
            "Unidade 1,Lab C,centro,REF_C\n"
            "Unidade 2,Outro predio,centro,REF_D\n",
            encoding="utf-8",
        )
        importar_salas(salas_csv, self.conexao)
        salas = {
            linha["nome"]: linha["id"]
            for linha in self.conexao.execute(
                "SELECT id, nome FROM sala WHERE unidade = 'Unidade 1'"
            )
        }

        bens = [
            ("100", "Localizado e leitura cruzada", "Lab A", "ASSET_LOCAL"),
            ("101", "Fora do local", "Lab A", "ASSET_OTHER"),
            ("102", "Nao localizado", "Lab A", "ASSET_MISSING"),
            ("103", "Sala ainda nao varrida", "Lab C", "ASSET_UNCOVERED"),
            ("104", "Sem etiqueta", "Lab A", None),
            ("200", "Outro inventario", "REF_D", "ASSET_OTHER_UNIT"),
        ]
        for patrimonio, descricao, sala, epc in bens:
            if patrimonio == "200":
                sala_esperada_id = self.conexao.execute(
                    "SELECT id FROM sala WHERE unidade = 'Unidade 2' AND nome = 'Outro predio'"
                ).fetchone()["id"]
            else:
                sala_esperada_id = salas[sala]
            self.conexao.execute(
                "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, epc, "
                "importado_em) VALUES (?, ?, ?, ?, '2026-10-08T10:00:00')",
                (patrimonio, descricao, sala_esperada_id, epc),
            )

        self.conexao.execute(
            "INSERT INTO sessao (id, unidade, operador) VALUES ('S1', 'Unidade 1', 'Operador')"
        )
        for numero, sala in enumerate(("Lab A", "Lab B"), start=1):
            self.conexao.execute(
                "INSERT INTO varredura (id, sessao_id, numero, sala_id, inicio, "
                "arquivo_origem, importado_em, linhas_brutas) "
                "VALUES (?, 'S1', ?, ?, '2026-10-08 10:00:00.000', 'x.csv', "
                "'2026-10-08T10:00:00', 1)",
                (f"S1-v{numero:02d}", numero, salas[sala]),
            )
        self.conexao.execute(
            "INSERT INTO varredura (id, sessao_id, numero, sala_id, inicio, "
            "arquivo_origem, importado_em, linhas_brutas) "
            "VALUES ('S1-v03', 'S1', 3, NULL, '2026-10-08 10:00:00.000', "
            "'x.csv', '2026-10-08T10:00:00', 1)"
        )

        leituras = [
            ("S1-v01", "ASSET_LOCAL"),
            ("S1-v02", "ASSET_LOCAL"),
            ("S1-v02", "ASSET_OTHER"),
            ("S1-v01", "UNREGISTERED_EPC"),
            ("S1-v03", "UNREGISTERED_NO_ROOM"),
            ("S1-v01", "REF_A"),
        ]
        for varredura, epc in leituras:
            self.conexao.execute(
                "INSERT INTO leitura (varredura_id, epc, leituras, primeira_leitura, "
                "ultima_leitura) VALUES (?, ?, 1, '2026-10-08 10:01:00.000', "
                "'2026-10-08 10:01:00.000')",
                (varredura, epc),
            )
        self.conexao.commit()

    def test_classifica_bens_por_sala_e_cobertura(self):
        relatorio = conferir_sessao(self.conexao, "S1")
        situacoes = {bem.patrimonio: bem for bem in relatorio.bens}

        self.assertEqual(situacoes["100"].situacao, LOCALIZADO)
        self.assertEqual(situacoes["100"].salas_lidas, ["Lab A", "Lab B"])
        self.assertEqual(situacoes["101"].situacao, FORA_DO_LOCAL)
        self.assertEqual(situacoes["101"].salas_lidas, ["Lab B"])
        self.assertEqual(situacoes["102"].situacao, NAO_LOCALIZADO)
        self.assertEqual(situacoes["103"].situacao, SEM_COBERTURA)
        self.assertEqual(situacoes["104"].situacao, NAO_LOCALIZADO)
        self.assertNotIn("200", situacoes)

    def test_lista_epc_desconhecido_e_ignora_tag_de_referencia(self):
        relatorio = conferir_sessao(self.conexao, "S1")

        self.assertEqual(
            [(epc.epc, epc.salas_lidas) for epc in relatorio.epcs_nao_cadastrados],
            [
                ("UNREGISTERED_EPC", ["Lab A"]),
                ("UNREGISTERED_NO_ROOM", ["sala nao identificada"]),
            ],
        )

    def test_sessao_inexistente_e_informada(self):
        with self.assertRaises(SessaoNaoEncontrada):
            conferir_sessao(self.conexao, "inexistente")


if __name__ == "__main__":
    unittest.main()
