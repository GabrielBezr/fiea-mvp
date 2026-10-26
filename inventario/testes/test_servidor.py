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


if __name__ == "__main__":
    unittest.main()
