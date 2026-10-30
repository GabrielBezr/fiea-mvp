"""Testes das rotas do servidor que separam salas de mesmo nome em unidades diferentes."""

import csv
import importlib.util
import io
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


@unittest.skipUnless(TEM_FLASK, "Flask não instalado (use o .venv do servidor)")
class TestRelatorioSalaProvavel(unittest.TestCase):
    """Relatório e CSV mostram a sala provável do bem lido em duas salas vizinhas."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self._db_original = servidor.CAMINHO_DB
        servidor.CAMINHO_DB = Path(self._temp.name) / "teste.db"
        self.addCleanup(setattr, servidor, "CAMINHO_DB", self._db_original)

        con = esquema.conectar(servidor.CAMINHO_DB)
        self.addCleanup(con.close)
        for nome in ("Lab A", "Lab B", "Lab C"):
            con.execute("INSERT INTO sala (unidade, nome) VALUES ('UFAL', ?)", (nome,))
        salas = {r["nome"]: r["id"] for r in con.execute("SELECT id, nome FROM sala")}
        for patrimonio, sala, epc in (("1", "Lab A", "AAA"), ("2", "Lab C", "CCC"),
                                      ("3", "Lab A", "DDD")):
            con.execute(
                "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, epc, importado_em) "
                "VALUES (?, ?, ?, ?, '2026-10-26')",
                (patrimonio, "Bem " + patrimonio, salas[sala], epc),
            )
        con.commit()
        self.cliente = servidor.app.test_client()

        # Lab A e Lab B varridos do mesmo jeito; o Lab C fica sem varredura.
        # AAA: mais forte no A (sala dele). CCC: parecido nos dois, e o Lab C nao foi
        # varrido. DDD: mais forte no B. EEE: sem cadastro, mais forte no A.
        sinais = {"Lab A": {"AAA": 82, "CCC": 61, "DDD": 60, "EEE": 80},
                  "Lab B": {"AAA": 71, "CCC": 63, "DDD": 80, "EEE": 60}}
        for numero, (sala, por_epc) in enumerate(sinais.items(), start=1):
            leituras = [
                {"epc": epc, "rssi": rssi, "metodo": "RFID",
                 "primeira_leitura": f"2026-10-26 10:0{numero}:0{i}.000",
                 "ultima_leitura": f"2026-10-26 10:0{numero}:0{i}.000"}
                for epc, rssi in por_epc.items() for i in range(2)
            ]
            resposta = self.cliente.post("/api/sincronizar", json={
                "sessao": {"id": "S4", "unidade": "UFAL", "operador": "Ana"},
                "varredura": {"id": f"S4-v0{numero}", "sala": sala, "tecnica": "movel_ambiente",
                              "potencia_dbm": 25, "inicio": "2026-10-26 10:00:00.000"},
                "leituras": leituras,
            })
            self.assertEqual(resposta.status_code, 200)

    def test_relatorio_mostra_sala_indeterminada_e_sala_provavel(self):
        resposta = self.cliente.get("/relatorio?sessao=S4")
        self.assertEqual(resposta.status_code, 200)
        html = resposta.get_data(as_text=True)

        self.assertIn("Sala Indeterminada (1)", html)
        self.assertIn('data-situacao="indeterminado"', html)
        self.assertIn("Varra a sala Lab C para decidir", html)
        self.assertIn("Provável: <strong>Lab B</strong>", html)
        self.assertIn("Transferência provável para Lab B", html)

    def test_csv_traz_a_sala_provavel_nas_ultimas_colunas(self):
        resposta = self.cliente.get("/relatorio/S4/exportar-csv")
        self.assertEqual(resposta.status_code, 200)
        linhas = list(csv.reader(io.StringIO(resposta.data.decode("utf-8-sig")), delimiter=";"))
        cabecalho, dados = linhas[0], {linha[0] if linha[0] != "N/A" else linha[2]: linha
                                       for linha in linhas[1:]}

        # As colunas antigas continuam no mesmo lugar.
        self.assertEqual(cabecalho[:9], ["Patrimonio", "Descricao", "EPC", "Sala_Esperada",
                                         "Salas_Lidas", "Situacao", "Unidade", "Sessao_ID",
                                         "Diagnostico"])
        self.assertEqual(cabecalho[9:], ["Sala_Provavel", "Criterio", "Sinal_Por_Sala"])
        self.assertEqual(dados["1"][5], "LOCALIZADO")
        self.assertEqual(dados["1"][9:], ["Lab A", "Sinal mais forte", "Lab A: 82; Lab B: 71"])
        self.assertEqual(dados["2"][5], "INDETERMINADO")
        self.assertEqual(dados["2"][9:11], ["", "Sala esperada não varrida"])
        self.assertEqual(dados["3"][5], "FORA DO LOCAL")
        self.assertEqual(dados["3"][9:11], ["Lab B", "Sinal mais forte"])
        self.assertEqual(dados["EEE"][9:11], ["Lab A", "Sinal mais forte"])


@unittest.skipUnless(TEM_FLASK, "Flask não instalado (use o .venv do servidor)")
class TestExclusaoSala(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self._db_original = servidor.CAMINHO_DB
        servidor.CAMINHO_DB = Path(self._temp.name) / "teste.db"
        self.addCleanup(setattr, servidor, "CAMINHO_DB", self._db_original)

        self.con = esquema.conectar(servidor.CAMINHO_DB)
        self.addCleanup(self.con.close)
        self.cliente = servidor.app.test_client()

        # Cria salas
        self.con.execute("INSERT INTO sala (unidade, nome) VALUES ('UFAL', 'Sala Vazia')")
        self.con.execute("INSERT INTO sala (unidade, nome) VALUES ('UFAL', 'Sala Com Bens')")
        self.con.execute("INSERT INTO sala (unidade, nome) VALUES ('UFAL', 'Sala Com Varreduras')")

        salas = {r["nome"]: r["id"] for r in self.con.execute("SELECT id, nome FROM sala")}
        self.id_vazia = salas["Sala Vazia"]
        self.id_com_bens = salas["Sala Com Bens"]
        self.id_com_varreduras = salas["Sala Com Varreduras"]

        # Bem vinculado
        self.con.execute(
            "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, importado_em) "
            "VALUES ('B1', 'Item 1', ?, '2026-10-27')",
            (self.id_com_bens,),
        )

        # Sessão e Varredura vinculada
        self.con.execute(
            "INSERT INTO sessao (id, unidade, operador, inicio) VALUES ('S_TESTE', 'UFAL', 'Op', '2026-10-27')"
        )
        self.con.execute(
            "INSERT INTO varredura (id, sessao_id, numero, sala_id, inicio, arquivo_origem, importado_em, linhas_brutas) "
            "VALUES ('S_TESTE-v01', 'S_TESTE', 1, ?, '2026-10-27', 'teste.csv', '2026-10-27', 0)",
            (self.id_com_varreduras,),
        )
        self.con.commit()

    def test_excluir_sala_vazia_tem_sucesso(self):
        resposta = self.cliente.post(f"/salas/excluir/{self.id_vazia}", follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("excluída com sucesso", resposta.get_data(as_text=True))
        linha = self.con.execute("SELECT id FROM sala WHERE id = ?", (self.id_vazia,)).fetchone()
        self.assertIsNone(linha)

    def test_excluir_sala_com_bens_e_bloqueado(self):
        resposta = self.cliente.post(f"/salas/excluir/{self.id_com_bens}", follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        texto = resposta.get_data(as_text=True)
        self.assertIn("Não é possível excluir", texto)
        self.assertIn("bem vinculado", texto)
        linha = self.con.execute("SELECT id FROM sala WHERE id = ?", (self.id_com_bens,)).fetchone()
        self.assertIsNotNone(linha)

    def test_excluir_sala_com_varredura_e_bloqueado_sem_erro_500(self):
        resposta = self.cliente.post(f"/salas/excluir/{self.id_com_varreduras}", follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        texto = resposta.get_data(as_text=True)
        self.assertIn("Não é possível excluir", texto)
        self.assertIn("varredura registrada", texto)
        linha = self.con.execute("SELECT id FROM sala WHERE id = ?", (self.id_com_varreduras,)).fetchone()
        self.assertIsNotNone(linha)

    def test_excluir_sala_inexistente(self):
        resposta = self.cliente.post("/salas/excluir/99999", follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Sala não encontrada", resposta.get_data(as_text=True))

    def test_listagem_mostra_contagem_de_varreduras(self):
        resposta = self.cliente.get("/salas")
        self.assertEqual(resposta.status_code, 200)
        texto = resposta.get_data(as_text=True)
        self.assertIn("1 varredura", texto)


@unittest.skipUnless(TEM_FLASK, "Flask não instalado (use o .venv do servidor)")
class TestAssociacaoTagEBens(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._temp.cleanup)
        self._db_original = servidor.CAMINHO_DB
        servidor.CAMINHO_DB = Path(self._temp.name) / "teste.db"
        self.addCleanup(setattr, servidor, "CAMINHO_DB", self._db_original)

        con = esquema.conectar(servidor.CAMINHO_DB)
        self.addCleanup(con.close)
        con.execute("INSERT INTO sala (unidade, nome) VALUES ('SENAI', 'Almoxarifado')")
        self.sala_id = con.execute("SELECT id FROM sala").fetchone()["id"]
        con.execute(
            "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, epc, metalico, importado_em) "
            "VALUES ('101', 'Monitor Dell', ?, 'E28011111111', 0, '2026-10-28')",
            (self.sala_id,)
        )
        con.commit()
        self.con = con
        self.cliente = servidor.app.test_client()

    def test_cadastro_bem_com_tag_duplicada_avisa_conflito(self):
        # Tenta cadastrar bem '102' com a mesma tag do bem '101'
        resposta = self.cliente.post("/cadastrar", data={
            "patrimonio": "102", "descricao": "Teclado", "sala": str(self.sala_id),
            "epc": "E28011111111"
        }, follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        html = resposta.get_data(as_text=True)
        self.assertIn("já está vinculada ao bem 101", html)
        self.assertIn("Transferir Tag para este Bem", html)

        # O bem 101 ainda tem a tag
        b101 = self.con.execute("SELECT epc FROM bem WHERE patrimonio = '101'").fetchone()
        self.assertEqual(b101["epc"], "E28011111111")
        # O bem 102 não foi criado com essa tag
        b102 = self.con.execute("SELECT epc FROM bem WHERE patrimonio = '102'").fetchone()
        self.assertIsNone(b102)

    def test_transferencia_de_tag_entre_bens_com_confirmacao(self):
        # Transfere a tag do bem '101' para o bem '102' sem marcar metalico
        resposta = self.cliente.post("/cadastrar", data={
            "patrimonio": "102", "descricao": "Teclado", "sala": str(self.sala_id),
            "epc": "E28011111111", "metalico": "0", "confirmar_transferencia": "1"
        }, follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        html = resposta.get_data(as_text=True)
        self.assertIn("Tag E28011111111 transferida do bem 101", html)

        # O bem 101 ficou sem tag
        b101 = self.con.execute("SELECT epc FROM bem WHERE patrimonio = '101'").fetchone()
        self.assertIsNone(b101["epc"])
        # O bem 102 agora tem a tag e NÃO é metálico
        b102 = self.con.execute("SELECT epc, metalico FROM bem WHERE patrimonio = '102'").fetchone()
        self.assertEqual(b102["epc"], "E28011111111")
        self.assertEqual(b102["metalico"], 0)

    def test_bloqueio_patrimonio_duplicado_no_cadastro(self):
        # Tenta cadastrar um novo bem usando o patrimônio '101' já existente
        resposta = self.cliente.post("/cadastrar", data={
            "patrimonio": "101", "descricao": "Tentativa Sobrescrever",
            "sala": str(self.sala_id), "editando": "0"
        }, follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        html = resposta.get_data(as_text=True)
        self.assertIn("já está cadastrado", html)
        self.assertIn("Cada patrimônio deve ser único", html)

        # Garante que os dados originais do bem 101 não foram sobrescritos
        b101 = self.con.execute("SELECT descricao FROM bem WHERE patrimonio = '101'").fetchone()
        self.assertEqual(b101["descricao"], "Monitor Dell")

    def test_api_verificar_patrimonio(self):
        # Patrimônio disponível
        r1 = self.cliente.get("/api/verificar-patrimonio?patrimonio=999")
        self.assertEqual(r1.get_json()["status"], "disponivel")

        # Patrimônio já em uso
        r2 = self.cliente.get("/api/verificar-patrimonio?patrimonio=101")
        dados = r2.get_json()
        self.assertEqual(dados["status"], "em_uso")
        self.assertEqual(dados["patrimonio"], "101")
        self.assertEqual(dados["descricao"], "Monitor Dell")
        self.assertEqual(dados["sala"], "Almoxarifado")

    def test_edicao_de_bem_permite_remover_epc(self):
        # Edita bem 101 com editando=1 e epc vazio: deve remover a tag
        resposta = self.cliente.post("/cadastrar", data={
            "patrimonio": "101", "descricao": "Monitor Dell Atualizado",
            "sala": str(self.sala_id), "epc": "", "editando": "1"
        }, follow_redirects=True)
        self.assertEqual(resposta.status_code, 200)
        b101 = self.con.execute("SELECT epc, descricao FROM bem WHERE patrimonio = '101'").fetchone()
        self.assertIsNone(b101["epc"])
        self.assertEqual(b101["descricao"], "Monitor Dell Atualizado")

    def test_api_verificar_epc(self):
        # Tag disponível
        r1 = self.cliente.get("/api/verificar-epc?epc=E28099999999")
        self.assertEqual(r1.get_json()["status"], "disponivel")

        # Tag em uso por outro bem
        r2 = self.cliente.get("/api/verificar-epc?epc=E28011111111&patrimonio=102")
        self.assertEqual(r2.get_json()["status"], "em_uso")
        self.assertEqual(r2.get_json()["patrimonio"], "101")
        self.assertEqual(r2.get_json()["sala"], "Almoxarifado")

        # Tag do próprio bem
        r3 = self.cliente.get("/api/verificar-epc?epc=E28011111111&patrimonio=101")
        self.assertEqual(r3.get_json()["status"], "proprio_bem")

    def test_rota_qrcode_js(self):
        # Entrega do script qrcode.min.js localmente
        resp = self.cliente.get("/js/qrcode.min.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/javascript", resp.headers.get("Content-Type", ""))
        self.assertIn("QRCode", resp.get_data(as_text=True))

    def test_cadastro_bem_sugere_impressao_etiqueta(self):
        # Cadastra bem novo e verifica que a tela sugere impressão com QR Code
        resp = self.cliente.post("/cadastrar", data={
            "patrimonio": "105", "descricao": "Cadeira Giratória",
            "sala": str(self.sala_id), "epc": "E28055555555"
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Imprimir Etiqueta / QR Code", html)
        self.assertIn("modalEtiqueta", html)
        self.assertIn("btn-abrir-etiqueta", html)


    def test_api_status_com_fila_offline_rastreia_coletor(self):
        # Health-check sem fila
        resp = self.cliente.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        dados = resp.get_json()
        self.assertEqual(dados.get("status"), "online")

        # Status do coletor sem fila
        resp_coletor = self.cliente.get("/api/status-coletor")
        self.assertEqual(resp_coletor.status_code, 200)
        dados_coletor = resp_coletor.get_json()
        self.assertTrue(dados_coletor.get("conectado"))
        self.assertEqual(dados_coletor.get("fila_pendente"), 0)

        # Health-check com fila=3
        resp = self.cliente.get("/api/status?fila=3")
        self.assertEqual(resp.status_code, 200)

        # Status do coletor agora reporta fila_pendente = 3
        resp_coletor = self.cliente.get("/api/status-coletor")
        self.assertEqual(resp_coletor.status_code, 200)
        dados_coletor = resp_coletor.get_json()
        self.assertTrue(dados_coletor.get("conectado"))
        self.assertEqual(dados_coletor.get("fila_pendente"), 3)

    def test_paginas_cadastro_contem_script_persistencia_rascunho(self):
        resp_bens = self.cliente.get("/bens")
        self.assertEqual(resp_bens.status_code, 200)
        html_bens = resp_bens.get_data(as_text=True)
        self.assertIn("CHAVE_RASCUNHO_BEM", html_bens)
        self.assertIn("btnLimparFormNovoBem", html_bens)
        self.assertIn("salvarRascunhoBem", html_bens)
        self.assertIn("restaurarRascunhoBem", html_bens)

        resp_salas = self.cliente.get("/salas")
        self.assertEqual(resp_salas.status_code, 200)
        html_salas = resp_salas.get_data(as_text=True)
        self.assertIn("CHAVE_RASCUNHO_SALA", html_salas)
        self.assertIn("campoNomeSala", html_salas)
        self.assertIn("btnLimparFormNovaSala", html_salas)
        self.assertIn("salvarRascunhoSala", html_salas)
        self.assertIn("restaurarRascunhoSala", html_salas)


if __name__ == "__main__":
    unittest.main()
