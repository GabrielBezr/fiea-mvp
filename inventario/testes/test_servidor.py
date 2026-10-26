"""Testes das rotas do servidor que separam salas de mesmo nome em unidades diferentes."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inventario import esquema  # noqa: E402

TEM_FLASK = importlib.util.find_spec("flask") is not None
if TEM_FLASK:
    from inventario import servidor  # noqa: E402


@unittest.skipUnless(TEM_FLASK, "Flask não instalado (use o .venv do servidor)")
class TestSalasPorUnidade(unittest.TestCase):
    def setUp(self):
        # O servidor abre conexões que não fecha; no Windows o arquivo fica preso até o fim.
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self._db_original = servidor.CAMINHO_DB
        servidor.CAMINHO_DB = Path(self._temp.name) / "teste.db"
        self.addCleanup(setattr, servidor, "CAMINHO_DB", self._db_original)

        con = esquema.conectar(servidor.CAMINHO_DB)
        self.addCleanup(con.close)
        for unidade, nome in (("UFAL", "Almoxarifado"), ("SENAI", "Almoxarifado"), ("UFAL", "Lab A")):
            con.execute("INSERT INTO sala (unidade, nome) VALUES (?, ?)", (unidade, nome))
        self.salas = {
            (linha["unidade"], linha["nome"]): linha["id"]
            for linha in con.execute("SELECT id, unidade, nome FROM sala")
        }
        for patrimonio, unidade in (("1", "UFAL"), ("2", "SENAI")):
            con.execute(
                "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, importado_em) "
                "VALUES (?, ?, ?, '2026-10-21')",
                (patrimonio, "Bem " + patrimonio, self.salas[(unidade, "Almoxarifado")]),
            )
        con.commit()
        self.con = con
        self.cliente = servidor.app.test_client()

    def _patrimonios(self, url):
        resposta = self.cliente.get(url)
        self.assertEqual(resposta.status_code, 200)
        return sorted(b["patrimonio"] for b in resposta.get_json())

    def test_bens_da_sala_ficam_na_unidade_pedida(self):
        self.assertEqual(self._patrimonios("/api/bens?sala=Almoxarifado&unidade=UFAL"), ["1"])
        self.assertEqual(self._patrimonios("/api/bens?sala=almoxarifado&unidade=SENAI"), ["2"])

    def test_sem_unidade_ou_com_unidade_desconhecida_filtra_so_pelo_nome(self):
        # Comportamento anterior, mantido para coletor com unidade digitada à mão.
        self.assertEqual(self._patrimonios("/api/bens?sala=Almoxarifado"), ["1", "2"])
        self.assertEqual(self._patrimonios("/api/bens?sala=Almoxarifado&unidade=Ufal"), ["1", "2"])

    def test_cadastro_de_bem_usa_o_id_da_sala(self):
        sala_senai = self.salas[("SENAI", "Almoxarifado")]
        resposta = self.cliente.post("/cadastrar", data={
            "patrimonio": "3", "descricao": "Mesa", "sala": str(sala_senai),
        })
        self.assertEqual(resposta.status_code, 302)
        linha = self.con.execute("SELECT sala_esperada_id FROM bem WHERE patrimonio = '3'").fetchone()
        self.assertEqual(linha["sala_esperada_id"], sala_senai)

    def test_cadastro_ainda_aceita_o_nome_da_sala(self):
        resposta = self.cliente.post("/cadastrar", data={
            "patrimonio": "4", "descricao": "Cadeira", "sala": "Lab A",
        })
        self.assertEqual(resposta.status_code, 302)
        linha = self.con.execute("SELECT sala_esperada_id FROM bem WHERE patrimonio = '4'").fetchone()
        self.assertEqual(linha["sala_esperada_id"], self.salas[("UFAL", "Lab A")])


@unittest.skipUnless(TEM_FLASK, "Flask não instalado (use o .venv do servidor)")
class TestSincronizacao(unittest.TestCase):
    """/api/sincronizar: o que o coletor manda pelo Wi-Fi tem de chegar inteiro."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self._db_original = servidor.CAMINHO_DB
        servidor.CAMINHO_DB = Path(self._temp.name) / "teste.db"
        self.addCleanup(setattr, servidor, "CAMINHO_DB", self._db_original)

        self.con = esquema.conectar(servidor.CAMINHO_DB)
        self.addCleanup(self.con.close)
        self.cliente = servidor.app.test_client()

    def _enviar(self, leituras, pendencias=None):
        # Mesmo formato do SyncManager.sincronizarVarredura: um evento por leitura.
        resposta = self.cliente.post("/api/sincronizar", json={
            "sessao": {"id": "S1", "unidade": "UFAL", "operador": "Ana"},
            "varredura": {"id": "S1-v01", "sala": "Lab A", "tecnica": "movel_ambiente",
                          "potencia_dbm": 25, "inicio": "2026-10-26 10:00:00.000",
                          "fim": "2026-10-26 10:02:00.000", "duracao_s": 120},
            "leituras": leituras,
            "pendencias": pendencias or [],
        })
        self.assertEqual(resposta.status_code, 200)

    @staticmethod
    def _evento(epc, rssi, instante):
        return {"epc": epc, "rssi": rssi, "metodo": "RFID",
                "primeira_leitura": instante, "ultima_leitura": instante}

    def _linhas(self):
        return {
            (r["epc"], r["metodo"]): dict(r)
            for r in self.con.execute("SELECT * FROM leitura WHERE varredura_id = 'S1-v01'")
        }

    def test_eventos_da_mesma_etiqueta_viram_uma_linha_com_contagem_e_rssi(self):
        self._enviar([
            self._evento("aaa", 60, "2026-10-26 10:00:01.000"),
            self._evento("AAA", 80, "2026-10-26 10:00:03.000"),
            self._evento("AAA", 70, "2026-10-26 10:00:02.000"),
            self._evento("BBB", 50, "2026-10-26 10:00:04.000"),
        ])
        linhas = self._linhas()

        self.assertEqual(set(linhas), {("AAA", "RFID"), ("BBB", "RFID")})
        aaa = linhas[("AAA", "RFID")]
        self.assertEqual(aaa["leituras"], 3)
        self.assertEqual((aaa["rssi_min"], aaa["rssi_medio"], aaa["rssi_max"]), (60, 70.0, 80))
        self.assertEqual(aaa["primeira_leitura"], "2026-10-26 10:00:01.000")
        self.assertEqual(aaa["ultima_leitura"], "2026-10-26 10:00:03.000")
        self.assertEqual(linhas[("BBB", "RFID")]["leituras"], 1)

    def test_leituras_ja_agregadas_continuam_aceitas(self):
        self._enviar([{"epc": "AAA", "metodo": "RFID", "leituras": 10, "rssi_min": 60,
                       "rssi_medio": 65.0, "rssi_max": 70,
                       "primeira_leitura": "2026-10-26 10:00:01.000",
                       "ultima_leitura": "2026-10-26 10:00:09.000"}])
        aaa = self._linhas()[("AAA", "RFID")]

        self.assertEqual((aaa["leituras"], aaa["rssi_min"], aaa["rssi_medio"], aaa["rssi_max"]),
                         (10, 60, 65.0, 70))

    def test_reenviar_a_mesma_varredura_nao_duplica(self):
        eventos = [self._evento("AAA", 60, "2026-10-26 10:00:01.000"),
                   self._evento("AAA", 80, "2026-10-26 10:00:02.000")]
        self._enviar(eventos)
        self._enviar(eventos)

        self.assertEqual(self._linhas()[("AAA", "RFID")]["leituras"], 2)

    def test_envio_antigo_sem_resolucoes_nao_apaga_as_ja_recebidas(self):
        # A fila offline pode entregar o envio do fim da varredura depois do que levou
        # as resoluções de Pendências.
        eventos = [self._evento("AAA", 60, "2026-10-26 10:00:01.000")]
        self._enviar(eventos, [{"patrimonio": "7", "epc": "BBB", "metodo": "MANUAL",
                                "timestamp": "2026-10-26 10:05:00.000"}])
        self._enviar(eventos)

        self.assertEqual(set(self._linhas()), {("AAA", "RFID"), ("BBB", "MANUAL")})

    def test_lista_nova_de_resolucoes_substitui_a_anterior(self):
        eventos = [self._evento("AAA", 60, "2026-10-26 10:00:01.000")]
        self._enviar(eventos, [{"patrimonio": "7", "epc": "BBB", "metodo": "MANUAL"}])
        self._enviar(eventos, [{"patrimonio": "8", "epc": "CCC", "metodo": "FALTANTE"}])

        self.assertEqual(set(self._linhas()), {("AAA", "RFID"), ("CCC", "FALTANTE")})


if __name__ == "__main__":
    unittest.main()
