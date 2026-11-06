"""Esquema do banco, seguindo o modelo de dados da secao 7.3 do Marco 1.

A tabela `leitura` ja nasce deduplicada: uma linha por (varredura, epc, metodo),
como o RF06 exige. As repeticoes nao somem, viram estatistica -- a contagem e o
RSSI sao a evidencia que a conciliacao (RF07/RF08) vai usar depois.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessao (
    id            TEXT PRIMARY KEY,
    unidade       TEXT NOT NULL,
    operador      TEXT NOT NULL,
    inicio        TEXT,
    fim           TEXT,
    duracao_s     INTEGER,
    n_varreduras  INTEGER,
    encerrada_auto INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS varredura (
    id              TEXT PRIMARY KEY,
    sessao_id       TEXT NOT NULL REFERENCES sessao(id),
    numero          INTEGER NOT NULL,
    epc_referencia  TEXT,
    sala_id         INTEGER REFERENCES sala(id),
    tecnica         TEXT,
    potencia_dbm    INTEGER,
    inicio          TEXT NOT NULL,
    fim             TEXT,
    duracao_s       INTEGER,
    arquivo_origem  TEXT NOT NULL,
    importado_em    TEXT NOT NULL,
    linhas_brutas   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS leitura (
    varredura_id      TEXT NOT NULL REFERENCES varredura(id) ON DELETE CASCADE,
    epc               TEXT NOT NULL,
    metodo            TEXT NOT NULL DEFAULT 'RFID',
    leituras          INTEGER NOT NULL,
    rssi_min          INTEGER,
    rssi_medio        REAL,
    rssi_max          INTEGER,
    primeira_leitura  TEXT NOT NULL,
    ultima_leitura    TEXT NOT NULL,
    PRIMARY KEY (varredura_id, epc, metodo)
);

-- RF01: cada sala da unidade, com N tags de referencia fixadas nela.
-- O modelo da secao 7.3 previa uma tag por sala; sao varias (4 cantos + centro),
-- por isso a tag virou tabela propria em vez de coluna.
CREATE TABLE IF NOT EXISTS sala (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    unidade  TEXT NOT NULL,
    nome     TEXT NOT NULL,
    UNIQUE (unidade, nome)
);

CREATE TABLE IF NOT EXISTS tag_referencia (
    epc      TEXT PRIMARY KEY,
    sala_id  INTEGER NOT NULL REFERENCES sala(id) ON DELETE CASCADE,
    posicao  TEXT NOT NULL,
    UNIQUE (sala_id, posicao)
);

-- RF02 preenche patrimonio, descricao e sala esperada; o RF03 preenche epc e
-- metalico, e uma reimportacao da relacao patrimonial nao os toca.
--
-- Nao ha coluna qr_code: o QR impresso na etiqueta carrega o proprio EPC, entao
-- guarda-lo separado seria gravar o mesmo valor duas vezes. O RF03 do documento
-- ainda fala em "EPC e QR code" como se fossem dois dados distintos.
CREATE TABLE IF NOT EXISTS bem (
    patrimonio        TEXT PRIMARY KEY,
    descricao         TEXT,
    sala_esperada_id  INTEGER REFERENCES sala(id),
    epc               TEXT UNIQUE,
    metalico          INTEGER,
    importado_em      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leitura_epc ON leitura(epc);
CREATE INDEX IF NOT EXISTS idx_varredura_sessao ON varredura(sessao_id);
CREATE INDEX IF NOT EXISTS idx_tag_sala ON tag_referencia(sala_id);
CREATE INDEX IF NOT EXISTS idx_bem_sala ON bem(sala_esperada_id);
"""


def conectar(caminho_db):
    """Abre (criando se preciso) o banco e garante o esquema."""
    caminho = Path(caminho_db)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(caminho)
    conexao.row_factory = sqlite3.Row
    conexao.executescript(DDL)
    colunas_varredura = {
        coluna["name"] for coluna in conexao.execute("PRAGMA table_info(varredura)")
    }
    if "sala_id" not in colunas_varredura:
        conexao.execute(
            "ALTER TABLE varredura ADD COLUMN sala_id INTEGER REFERENCES sala(id)"
        )
    colunas_sessao = {
        coluna["name"] for coluna in conexao.execute("PRAGMA table_info(sessao)")
    }
    if "encerrada_auto" not in colunas_sessao:
        conexao.execute(
            "ALTER TABLE sessao ADD COLUMN encerrada_auto INTEGER DEFAULT 0"
        )

    # Limpa sessões legadas com fim zerado (1969/1970) ou duração negativa
    conexao.execute(
        "UPDATE sessao SET fim = NULL, duracao_s = NULL WHERE fim LIKE '1969-%' OR fim LIKE '1970-%' OR duracao_s < 0"
    )

    # Corrige sessões recentes ativas que foram marcadas com encerrada_auto = 1 indevidamente
    conexao.execute("""
        UPDATE sessao SET encerrada_auto = 0
        WHERE encerrada_auto = 1
          AND inicio >= date('now', 'localtime')
          AND fim IS NULL
    """)

    # Fechamento automático às 23:59: sessões passadas/abandonadas que ficaram sem fim
    # 1. Sessões com varreduras concluídas
    linhas = conexao.execute("""
        SELECT s.id, s.inicio, MAX(v.fim) AS ultimo_fim
        FROM sessao s
        JOIN varredura v ON v.sessao_id = s.id
        WHERE (s.fim IS NULL OR s.duracao_s IS NULL)
          AND s.encerrada_auto = 0
          AND v.fim IS NOT NULL AND v.fim NOT LIKE '1969-%' AND v.fim NOT LIKE '1970-%'
          AND (
              date(s.inicio) < date('now', 'localtime')
              OR (s.id < (SELECT COALESCE(MAX(id), '') FROM sessao) AND s.inicio < datetime('now', '-4 hours'))
          )
        GROUP BY s.id
    """).fetchall()

    for r in linhas:
        s_id = r["id"]
        ini_str = r["inicio"]
        fim_str = r["ultimo_fim"]
        dur = 0
        try:
            dt_ini = None
            dt_fim = None
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt_ini = datetime.strptime(str(ini_str)[:19], fmt[:19])
                    break
                except ValueError:
                    continue
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt_fim = datetime.strptime(str(fim_str)[:19], fmt[:19])
                    break
                except ValueError:
                    continue
            if dt_ini and dt_fim:
                dur = max(0, int((dt_fim - dt_ini).total_seconds()))
        except Exception:
            dur = 0

        conexao.execute(
            "UPDATE sessao SET fim = ?, duracao_s = ?, encerrada_auto = 1 WHERE id = ?",
            (fim_str, dur, s_id),
        )

    # 2. Sessões de datas anteriores sem nenhuma varredura concluída (expiradas às 23:59)
    linhas_sem_var = conexao.execute("""
        SELECT s.id, s.inicio
        FROM sessao s
        WHERE (s.fim IS NULL OR s.duracao_s IS NULL)
          AND date(s.inicio) < date('now', 'localtime')
          AND NOT EXISTS (
              SELECT 1 FROM varredura v
              WHERE v.sessao_id = s.id AND v.fim IS NOT NULL AND v.fim NOT LIKE '1969-%' AND v.fim NOT LIKE '1970-%'
          )
    """).fetchall()

    for r in linhas_sem_var:
        s_id = r["id"]
        ini_str = str(r["inicio"])
        data_dia = ini_str[:10]
        fim_dia = f"{data_dia} 23:59:59"
        dur = 0
        try:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt_ini = datetime.strptime(ini_str[:19], fmt[:19])
                    dt_fim = datetime.strptime(fim_dia, "%Y-%m-%d %H:%M:%S")
                    dur = max(0, int((dt_fim - dt_ini).total_seconds()))
                    break
                except ValueError:
                    pass
        except Exception:
            dur = 0

        conexao.execute(
            "UPDATE sessao SET fim = ?, duracao_s = ?, encerrada_auto = 1 WHERE id = ?",
            (fim_dia, dur, s_id),
        )

    conexao.commit()
    return conexao
