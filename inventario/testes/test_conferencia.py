"""Testes da conciliacao de bens com as leituras de uma sessao."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventario import conferencia, esquema  # noqa: E402
from inventario.conferencia import (  # noqa: E402
    CRITERIO_CADASTRO, CRITERIO_CONFIRMADO, CRITERIO_EMPATE, CRITERIO_SALA_NAO_VARRIDA,
    CRITERIO_SINAL, CRITERIO_UNICA, FORA_DO_LOCAL, INDETERMINADO, LOCALIZADO,
    NAO_CADASTRADO, NAO_LOCALIZADO, SEM_COBERTURA, SessaoNaoEncontrada, conferir_sessao,
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

    def _sala_id(self, sala):
        return self.conexao.execute(
            "SELECT id FROM sala WHERE unidade = 'Unidade 1' AND nome = ?", (sala,)
        ).fetchone()["id"]

    def _bem(self, patrimonio, sala, epc):
        self.conexao.execute(
            "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, epc, importado_em) "
            "VALUES (?, 'Bem de teste', ?, ?, '2026-10-08T10:00:00')",
            (patrimonio, self._sala_id(sala), epc),
        )

    def _varredura(self, varredura, numero, sala):
        self.conexao.execute(
            "INSERT INTO varredura (id, sessao_id, numero, sala_id, inicio, "
            "arquivo_origem, importado_em, linhas_brutas) "
            "VALUES (?, 'S1', ?, ?, '2026-10-08 10:00:00.000', 'x.csv', "
            "'2026-10-08T10:00:00', 1)",
            (varredura, numero, self._sala_id(sala)),
        )

    def _rfid(self, varredura, epc, rssi_max, leituras=5, rssi_medio=None, rssi_min=None):
        self.conexao.execute(
            "INSERT INTO leitura (varredura_id, epc, metodo, leituras, rssi_min, rssi_medio, "
            "rssi_max, primeira_leitura, ultima_leitura) VALUES (?, ?, 'RFID', ?, ?, ?, ?, "
            "'2026-10-08 10:05:00.000', '2026-10-08 10:05:00.000')",
            (varredura, epc, leituras,
             rssi_max if rssi_min is None else rssi_min,
             rssi_max if rssi_medio is None else rssi_medio,
             rssi_max),
        )

    def _conferir(self):
        self.conexao.commit()
        relatorio = conferir_sessao(self.conexao, "S1")
        return {bem.patrimonio: bem for bem in relatorio.bens}, relatorio

    def _assert_sala(self, bem, situacao, sala_provavel, criterio):
        self.assertEqual((bem.situacao, bem.sala_provavel, bem.criterio),
                         (situacao, sala_provavel, criterio))

    def _leitura(self, varredura, epc, metodo):
        self.conexao.execute(
            "INSERT INTO leitura (varredura_id, epc, metodo, leituras, primeira_leitura, "
            "ultima_leitura) VALUES (?, ?, ?, 1, '2026-10-08 10:05:00.000', "
            "'2026-10-08 10:05:00.000')",
            (varredura, epc, metodo),
        )

    def test_resolucoes_de_pendencias_seguem_o_metodo(self):
        # S1-v01 e a varredura do Lab A; S1-v02, a do Lab B.
        self._bem("300", "Lab A", "ASSET_FALTANTE")
        self._leitura("S1-v01", "ASSET_FALTANTE", "FALTANTE")
        self._bem("301", "Lab A", None)
        self._leitura("S1-v01", "PAT_301", "MANUAL")
        self._bem("302", "Lab A", "ASSET_QR")
        self._leitura("S1-v01", "ASSET_QR", "QR")
        self._bem("303", "Lab A", "ASSET_FALTANTE_E_RADIO")
        self._leitura("S1-v01", "ASSET_FALTANTE_E_RADIO", "RFID")
        self._leitura("S1-v01", "ASSET_FALTANTE_E_RADIO", "FALTANTE")
        self._bem("304", "Lab A", "ASSET_FALTANTE_NA_VIZINHA")
        self._leitura("S1-v01", "ASSET_FALTANTE_NA_VIZINHA", "FALTANTE")
        self._leitura("S1-v02", "ASSET_FALTANTE_NA_VIZINHA", "RFID")
        self._bem("305", "Lab A", "ASSET_ACHADO_DEPOIS")
        self._leitura("S1-v01", "ASSET_ACHADO_DEPOIS", "FALTANTE")
        self._leitura("S1-v01", "ASSET_ACHADO_DEPOIS", "MANUAL")
        self.conexao.commit()

        relatorio = conferir_sessao(self.conexao, "S1")
        bens = {bem.patrimonio: bem for bem in relatorio.bens}

        # Faltante e o operador dizendo que procurou e nao achou: nao e localizado.
        self.assertEqual(bens["300"].situacao, NAO_LOCALIZADO)
        self.assertEqual(bens["300"].salas_lidas, [])
        # Manual de bem sem EPC chega como PAT_<patrimonio> e casa pelo patrimonio.
        self.assertEqual(bens["301"].situacao, LOCALIZADO)
        self.assertEqual(bens["302"].situacao, LOCALIZADO)
        # O faltante tira a sala onde foi marcado, mesmo com leitura do radio nela...
        self.assertEqual(bens["303"].situacao, NAO_LOCALIZADO)
        # ...mas nao a sala vizinha, onde o radio o leu.
        self.assertEqual(bens["304"].situacao, FORA_DO_LOCAL)
        self.assertEqual(bens["304"].salas_lidas, ["Lab B"])
        # Achado depois na mesma sala: a confirmacao vale mais que o faltante.
        self.assertEqual(bens["305"].situacao, LOCALIZADO)

    def test_metodo_de_localizacao_distingue_rfid_qr_e_manual(self):
        self._bem("310", "Lab A", "ASSET_RADIO")
        self._rfid("S1-v01", "ASSET_RADIO", 80)
        self._bem("311", "Lab A", "ASSET_QR2")
        self._leitura("S1-v01", "ASSET_QR2", "QR")
        self._bem("312", "Lab A", None)
        self._leitura("S1-v01", "PAT_312", "MANUAL")
        self._bem("313", "Lab A", "ASSET_QR_E_MANUAL")
        self._leitura("S1-v01", "ASSET_QR_E_MANUAL", "MANUAL")
        self._leitura("S1-v01", "ASSET_QR_E_MANUAL", "QR")
        self._bem("314", "Lab A", "ASSET_NADA")
        bens, _ = self._conferir()

        self.assertEqual(bens["310"].metodo, "RFID")
        self.assertEqual(bens["311"].metodo, "QR")
        self.assertEqual(bens["312"].metodo, "MANUAL")
        self.assertEqual(bens["313"].metodo, "QR")  # QR vale mais que manual
        self.assertEqual(bens["314"].metodo, "")    # nao localizado: sem metodo

    def test_resumo_de_metodos_conta_so_os_localizados(self):
        self._bem("310", "Lab A", "ASSET_RADIO")
        self._rfid("S1-v01", "ASSET_RADIO", 80)
        self._bem("311", "Lab A", "ASSET_QR2")
        self._leitura("S1-v01", "ASSET_QR2", "QR")
        self._bem("312", "Lab A", None)
        self._leitura("S1-v01", "PAT_312", "MANUAL")
        _, relatorio = self._conferir()

        resumo = conferencia.resumir_metodos(relatorio.bens)

        # O bem 100 do setUp (lido em Lab A e Lab B, vale o cadastro) tambem e RFID.
        self.assertEqual((resumo.localizados, resumo.rfid, resumo.qr, resumo.manual), (4, 2, 1, 1))
        self.assertEqual(resumo.total_bens, len(relatorio.bens))
        self.assertEqual(resumo.pct_rfid, 50.0)
        self.assertFalse(resumo.atingiu_meta)

    def test_meta_de_90_por_cento_de_rfid(self):
        def bem(metodo):
            return conferencia.BemConferido("1", "", "", "Sala", LOCALIZADO, metodo=metodo)

        noventa = conferencia.resumir_metodos([bem("RFID")] * 9 + [bem("QR")])
        self.assertEqual(noventa.pct_rfid, 90.0)
        self.assertTrue(noventa.atingiu_meta)

        abaixo = conferencia.resumir_metodos([bem("RFID")] * 8 + [bem("QR"), bem("MANUAL")])
        self.assertFalse(abaixo.atingiu_meta)

        vazio = conferencia.resumir_metodos([])
        self.assertEqual(vazio.pct_rfid, 0.0)
        self.assertFalse(vazio.atingiu_meta)

    def test_resolucoes_nao_entram_como_epc_nao_cadastrado(self):
        self._leitura("S1-v01", "PAT_999", "MANUAL")
        self._leitura("S1-v01", "HTTPS://BEONTAG.EXEMPLO/QR", "QR")
        self.conexao.commit()

        relatorio = conferir_sessao(self.conexao, "S1")

        self.assertEqual(
            [epc.epc for epc in relatorio.epcs_nao_cadastrados],
            ["UNREGISTERED_EPC", "UNREGISTERED_NO_ROOM"],
        )

    # Regra da sala provavel. S1-v01 varre o Lab A e S1-v02, o Lab B; o Lab C so tem
    # varredura quando o teste cria uma.

    def test_sinal_claramente_mais_forte_decide_a_sala(self):
        self._bem("400", "Lab A", "E400")
        self._rfid("S1-v01", "E400", 80)
        self._rfid("S1-v02", "E400", 70)
        self._bem("401", "Lab A", "E401")
        self._rfid("S1-v01", "E401", 62)
        self._rfid("S1-v02", "E401", 75)
        self._bem("402", "Lab A", "E402")  # diferenca exatamente igual a margem
        self._rfid("S1-v01", "E402", 70)
        self._rfid("S1-v02", "E402", 70 + conferencia.MARGEM_SINAL)
        bens, _ = self._conferir()

        self._assert_sala(bens["400"], LOCALIZADO, "Lab A", CRITERIO_SINAL)
        self.assertEqual(bens["400"].sinal_por_sala, {"Lab A": 80, "Lab B": 70})
        self._assert_sala(bens["401"], FORA_DO_LOCAL, "Lab B", CRITERIO_SINAL)
        self._assert_sala(bens["402"], FORA_DO_LOCAL, "Lab B", CRITERIO_SINAL)

    def test_empate_no_sinal_fica_com_o_cadastro_ou_sem_sala(self):
        self._varredura("S1-v04", 4, "Lab C")
        self._bem("410", "Lab A", "E410")
        self._rfid("S1-v01", "E410", 70)
        self._rfid("S1-v02", "E410", 74)
        self._bem("411", "Lab C", "E411")  # e do Lab C, lido nas outras duas
        self._rfid("S1-v01", "E411", 70)
        self._rfid("S1-v02", "E411", 74)
        self._bem("412", "Lab C", "E412")
        self._rfid("S1-v01", "E412", 80)
        self._rfid("S1-v02", "E412", 70)
        bens, _ = self._conferir()

        self._assert_sala(bens["410"], LOCALIZADO, "Lab A", CRITERIO_CADASTRO)
        self.assertEqual(bens["410"].salas_lidas, ["Lab A", "Lab B"])
        self._assert_sala(bens["411"], INDETERMINADO, "", CRITERIO_EMPATE)
        self._assert_sala(bens["412"], FORA_DO_LOCAL, "Lab A", CRITERIO_SINAL)

    def test_lido_so_fora_com_a_sala_esperada_sem_varredura(self):
        self._bem("420", "Lab C", "E420")
        self._rfid("S1-v02", "E420", 80)
        self._bem("421", "Lab C", "E421")
        self._leitura("S1-v02", "E421", "QR")
        self._bem("422", "Lab C", "E422")  # empate entre A e B, e o Lab C sem varredura
        self._rfid("S1-v01", "E422", 70)
        self._rfid("S1-v02", "E422", 72)
        bens, _ = self._conferir()

        # Pode ser so o sinal do Lab C atravessando a parede: sem varrer o Lab C, nao da.
        self._assert_sala(bens["420"], INDETERMINADO, "", CRITERIO_SALA_NAO_VARRIDA)
        self.assertEqual(bens["420"].salas_lidas, ["Lab B"])
        # O motivo que diz o que fazer (varrer o Lab C) vem antes do empate.
        self._assert_sala(bens["422"], INDETERMINADO, "", CRITERIO_SALA_NAO_VARRIDA)
        # Ja o operador confirmando no Lab B e prova de que ele esta la.
        self._assert_sala(bens["421"], FORA_DO_LOCAL, "Lab B", CRITERIO_CONFIRMADO)

    def test_confirmacao_do_operador_vale_mais_que_o_sinal(self):
        self._bem("425", "Lab A", "E425")
        self._rfid("S1-v01", "E425", 90)
        self._leitura("S1-v02", "E425", "MANUAL")
        bens, _ = self._conferir()

        self._assert_sala(bens["425"], FORA_DO_LOCAL, "Lab B", CRITERIO_CONFIRMADO)

    def test_so_compara_sinal_medido_do_mesmo_jeito(self):
        self.conexao.execute(
            "UPDATE varredura SET tecnica = 'movel_ambiente', potencia_dbm = 25 WHERE id = 'S1-v01'")
        self.conexao.execute(
            "UPDATE varredura SET tecnica = 'estatica_porta', potencia_dbm = 25 WHERE id = 'S1-v02'")
        self._bem("430", "Lab B", "E430")
        self._rfid("S1-v01", "E430", 90)
        self._rfid("S1-v02", "E430", 60)
        bens, _ = self._conferir()

        self._assert_sala(bens["430"], LOCALIZADO, "Lab B", CRITERIO_CADASTRO)

    def test_leitura_unica_ou_rssi_zerado_nao_entram_na_comparacao(self):
        self._bem("440", "Lab B", "E440")
        self._rfid("S1-v01", "E440", 90, leituras=1)
        self._rfid("S1-v02", "E440", 60)
        self._bem("441", "Lab B", "E441")  # como chegava pelo Wi-Fi antes da correcao
        self._rfid("S1-v01", "E441", 0, leituras=3)
        self._rfid("S1-v02", "E441", 60)
        bens, _ = self._conferir()

        self._assert_sala(bens["440"], LOCALIZADO, "Lab B", CRITERIO_CADASTRO)
        self._assert_sala(bens["441"], LOCALIZADO, "Lab B", CRITERIO_CADASTRO)
        self.assertEqual(bens["441"].sinal_por_sala, {"Lab B": 60})

    def test_sala_varrida_duas_vezes_usa_o_sinal_mais_forte(self):
        self._varredura("S1-v05", 5, "Lab A")
        self._bem("450", "Lab B", "E450")
        self._rfid("S1-v01", "E450", 60)
        self._rfid("S1-v05", "E450", 85)
        self._rfid("S1-v02", "E450", 75)
        bens, _ = self._conferir()

        self._assert_sala(bens["450"], FORA_DO_LOCAL, "Lab A", CRITERIO_SINAL)
        self.assertEqual(bens["450"].sinal_por_sala, {"Lab A": 85, "Lab B": 75})

    def test_convencao_e_metrica_do_rssi_sao_configuraveis(self):
        self._bem("460", "Lab A", "E460")
        self._rfid("S1-v01", "E460", 70, rssi_min=60)
        self._rfid("S1-v02", "E460", 80, rssi_min=75)
        self._bem("461", "Lab A", "E461")
        self._rfid("S1-v01", "E461", 80, rssi_medio=60)
        self._rfid("S1-v02", "E461", 70, rssi_medio=72)

        bens, _ = self._conferir()
        self._assert_sala(bens["460"], FORA_DO_LOCAL, "Lab B", CRITERIO_SINAL)
        self._assert_sala(bens["461"], LOCALIZADO, "Lab A", CRITERIO_SINAL)

        # Coletor em que o numero menor e o sinal mais forte: vale o menor RSSI.
        with mock.patch.object(conferencia, "RSSI_MAIOR_E_MAIS_FORTE", False):
            bens, _ = self._conferir()
        self._assert_sala(bens["460"], LOCALIZADO, "Lab A", CRITERIO_SINAL)
        self.assertEqual(bens["460"].sinal_por_sala, {"Lab A": 60, "Lab B": 75})

        with mock.patch.object(conferencia, "METRICA_SINAL", "medio"):
            bens, _ = self._conferir()
        self._assert_sala(bens["461"], FORA_DO_LOCAL, "Lab B", CRITERIO_SINAL)

    def test_epc_nao_cadastrado_tambem_ganha_sala_provavel(self):
        self._rfid("S1-v01", "UNREG_DUAS_SALAS", 80)
        self._rfid("S1-v02", "UNREG_DUAS_SALAS", 60)
        _, relatorio = self._conferir()
        epcs = {epc.epc: epc for epc in relatorio.epcs_nao_cadastrados}

        self.assertEqual((epcs["UNREG_DUAS_SALAS"].sala_provavel,
                          epcs["UNREG_DUAS_SALAS"].criterio), ("Lab A", CRITERIO_SINAL))
        self.assertEqual((epcs["UNREGISTERED_EPC"].sala_provavel,
                          epcs["UNREGISTERED_EPC"].criterio), ("Lab A", CRITERIO_UNICA))
        self.assertEqual(epcs["UNREGISTERED_NO_ROOM"].sala_provavel, "")

    def test_sessao_inexistente_e_informada(self):
        with self.assertRaises(SessaoNaoEncontrada):
            conferir_sessao(self.conexao, "inexistente")


if __name__ == "__main__":
    unittest.main()
