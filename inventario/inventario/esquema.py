"""Esquema do banco, seguindo o modelo de dados da secao 7.3 do Marco 1.

A tabela `leitura` ja nasce deduplicada: uma linha por (varredura, epc, metodo),
como o RF06 exige. As repeticoes nao somem, viram estatistica -- a contagem e o
RSSI sao a evidencia que a conciliacao (RF07/RF08) vai usar depois.
"""

import sqlite3
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
    n_varreduras  INTEGER
);

CREATE TABLE IF NOT EXISTS varredura (
    id              TEXT PRIMARY KEY,
    sessao_id       TEXT NOT NULL REFERENCES sessao(id),
    numero          INTEGER NOT NULL,
    epc_referencia  TEXT,
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
    conexao.commit()
    return conexao
