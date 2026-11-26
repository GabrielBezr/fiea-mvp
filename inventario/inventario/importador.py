"""Modulo de importacao (secao 7.2): recebe o CSV, valida e grava no banco.

Responde ao RF06. A transferencia do arquivo para o computador e deliberadamente
externa a este modulo: hoje o operador copia por USB para uma pasta, e amanha um
envio pela rede pode chamar `importar_arquivo` sem que nada aqui mude.
"""

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import leitor_csv
from .leitor_csv import ArquivoInvalido


@dataclass
class Resultado:
    """O que uma importacao fez, para o relato na linha de comando."""
    arquivo: str
    tipo: str
    identificador: str
    linhas_brutas: int = 0
    etiquetas_distintas: int = 0
    linhas_descartadas: int = 0
    reimportado: bool = False
    sala: str = ""
    epc_referencia: str = ""
    pendencias: int = 0


def importar_caminho(caminho, conexao, pasta_brutos=None):
    """Importa um arquivo ou todos os CSV de uma pasta.

    As varreduras entram antes das sessoes so por organizacao do relato; a ordem
    nao importa, porque cada lado completa o que falta no outro.
    """
    caminho = Path(caminho)
    if caminho.is_dir():
        arquivos = sorted(caminho.glob("*.csv"))
    else:
        arquivos = [caminho]

    resultados = []
    for arquivo in arquivos:
        resultados.append(importar_arquivo(arquivo, conexao, pasta_brutos))
    return resultados


def importar_arquivo(caminho, conexao, pasta_brutos=None):
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ArquivoInvalido(f"{caminho}: arquivo nao encontrado")

    tipo = leitor_csv.tipo_do_arquivo(caminho)
    if tipo == "varredura":
        resultado = _importar_varredura(caminho, conexao)
    elif tipo == "pendencia":
        resultado = _importar_pendencias(caminho, conexao)
    else:
        resultado = _importar_sessao(caminho, conexao)

    conexao.commit()
    if pasta_brutos is not None:
        _arquivar(caminho, pasta_brutos)
    return resultado


def _importar_varredura(caminho, conexao):
    arquivo = leitor_csv.ler_varredura(caminho)
    sala = None
    if arquivo.sala:
        sala = conexao.execute(
            "SELECT s.id, s.nome FROM sala s "
            "WHERE LOWER(s.nome) = LOWER(?) AND s.unidade = ?",
            (arquivo.sala, arquivo.unidade),
        ).fetchone()
        if not sala:
            sala = conexao.execute(
                "SELECT s.id, s.nome FROM sala s WHERE LOWER(s.nome) = LOWER(?)",
                (arquivo.sala,),
            ).fetchone()
        if not sala:
            conexao.execute(
                "INSERT INTO sala (unidade, nome) VALUES (?, ?) "
                "ON CONFLICT(unidade, nome) DO NOTHING",
                (arquivo.unidade, arquivo.sala),
            )
            sala = conexao.execute(
                "SELECT s.id, s.nome FROM sala s "
                "WHERE LOWER(s.nome) = LOWER(?) AND s.unidade = ?",
                (arquivo.sala, arquivo.unidade),
            ).fetchone()
    if not sala and arquivo.epc_referencia:
        sala = conexao.execute(
            "SELECT s.id, s.nome FROM tag_referencia t "
            "JOIN sala s ON s.id = t.sala_id "
            "WHERE t.epc = ? AND s.unidade = ?",
            (arquivo.epc_referencia, arquivo.unidade),
        ).fetchone()

    ja_existia = conexao.execute(
        "SELECT 1 FROM varredura WHERE id = ?", (arquivo.varredura_id,)
    ).fetchone() is not None

    # A sessao pode ainda nao ter sido importada: o aplicativo so exporta o
    # arquivo de sessao no encerramento. O proprio arquivo de leituras carrega
    # unidade e operador, o bastante para abrir o registro; inicio, fim e
    # duracao chegam depois, com o sessao_*.csv.
    conexao.execute(
        "INSERT INTO sessao (id, unidade, operador) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET unidade = excluded.unidade, "
        "operador = excluded.operador",
        (arquivo.sessao_id, arquivo.unidade, arquivo.operador),
    )

    conexao.execute(
        "INSERT INTO varredura (id, sessao_id, numero, epc_referencia, sala_id, tecnica, "
        "potencia_dbm, inicio, fim, duracao_s, arquivo_origem, importado_em, linhas_brutas) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "  sessao_id = excluded.sessao_id, numero = excluded.numero, "
        "  epc_referencia = excluded.epc_referencia, sala_id = excluded.sala_id, "
        "  tecnica = excluded.tecnica, "
        "  potencia_dbm = excluded.potencia_dbm, inicio = excluded.inicio, "
        "  fim = excluded.fim, duracao_s = excluded.duracao_s, "
        "  arquivo_origem = excluded.arquivo_origem, "
        "  importado_em = excluded.importado_em, linhas_brutas = excluded.linhas_brutas",
        (
            arquivo.varredura_id, arquivo.sessao_id, arquivo.numero,
            arquivo.epc_referencia, sala["id"] if sala else None,
            arquivo.tecnica, arquivo.potencia_dbm,
            arquivo.inicio, arquivo.fim, arquivo.duracao_s,
            caminho.name, datetime.now().isoformat(timespec="seconds"),
            arquivo.linhas_brutas,
        ),
    )

    # Reimportar substitui: o estado final e o mesmo tendo o arquivo entrado
    # uma ou cinco vezes, e uma reexportacao corrigida sobrepoe a anterior.
    # So as leituras do radio: o CSV nao traz as resolucoes de Pendencias (QR,
    # manual, faltante), que chegam pelo Wi-Fi e se perdiam aqui.
    conexao.execute(
        "DELETE FROM leitura WHERE varredura_id = ? AND metodo = 'RFID'",
        (arquivo.varredura_id,),
    )
    conexao.executemany(
        "INSERT INTO leitura (varredura_id, epc, metodo, leituras, rssi_min, "
        "rssi_medio, rssi_max, primeira_leitura, ultima_leitura) "
        "VALUES (?, ?, 'RFID', ?, ?, ?, ?, ?, ?)",
        [
            (
                arquivo.varredura_id, l.epc, l.leituras, l.rssi_min,
                l.rssi_medio, l.rssi_max, l.primeira_leitura, l.ultima_leitura,
            )
            for l in arquivo.leituras
        ],
    )

    return Resultado(
        arquivo=caminho.name,
        tipo="varredura",
        identificador=arquivo.varredura_id,
        linhas_brutas=arquivo.linhas_brutas,
        etiquetas_distintas=arquivo.etiquetas_distintas,
        linhas_descartadas=arquivo.linhas_descartadas,
        reimportado=ja_existia,
        sala=sala["nome"] if sala else "",
        epc_referencia=arquivo.epc_referencia,
    )


def gravar_resolucoes(conexao, varredura_id, pendencias):
    """Grava as resolucoes de Pendencias (QR, MANUAL, FALTANTE) de uma varredura.

    Caminho unico para o Wi-Fi (/api/sincronizar) e para o CSV do USB. A lista
    recebida e o estado completo da varredura: substitui a anterior, e uma lista
    vazia nao mexe nas resolucoes ja gravadas.
    """
    if not pendencias:
        return
    conexao.execute(
        "DELETE FROM leitura WHERE varredura_id = ? AND metodo != 'RFID'", (varredura_id,)
    )
    for p in pendencias:
        metodo = (p.get("metodo") or "MANUAL").upper()
        epc = (p.get("epc") or "").strip().upper()
        patrimonio = (p.get("patrimonio") or "").strip()

        # Se o EPC nao veio direto, procura se o bem ja tinha EPC cadastrado
        if not epc and patrimonio:
            b_row = conexao.execute(
                "SELECT epc FROM bem WHERE patrimonio = ?", (patrimonio,)
            ).fetchone()
            if b_row and b_row["epc"]:
                epc = b_row["epc"]
            else:
                epc = f"PAT_{patrimonio}"

        agora = p.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S.000")
        conexao.execute(
            "INSERT INTO leitura (varredura_id, epc, metodo, leituras, rssi_min, "
            "rssi_medio, rssi_max, primeira_leitura, ultima_leitura) "
            "VALUES (?, ?, ?, 1, 0, 0, 0, ?, ?) "
            "ON CONFLICT(varredura_id, epc, metodo) DO NOTHING",
            (varredura_id, epc, metodo, agora, agora),
        )


def _importar_pendencias(caminho, conexao):
    arquivo = leitor_csv.ler_pendencias(caminho)
    if arquivo.pendencias:
        existe = conexao.execute(
            "SELECT 1 FROM varredura WHERE id = ?", (arquivo.varredura_id,)
        ).fetchone()
        if not existe:
            raise ArquivoInvalido(
                f"{caminho.name}: a varredura {arquivo.varredura_id} ainda nao foi "
                f"importada; importe antes o leituras_*.csv dela"
            )
        gravar_resolucoes(conexao, arquivo.varredura_id, arquivo.pendencias)

    return Resultado(
        arquivo=caminho.name,
        tipo="pendencia",
        identificador=arquivo.varredura_id,
        pendencias=len(arquivo.pendencias),
    )


def _importar_sessao(caminho, conexao):
    arquivo = leitor_csv.ler_sessao(caminho)

    ja_existia = conexao.execute(
        "SELECT inicio FROM sessao WHERE id = ?", (arquivo.sessao_id,)
    ).fetchone()
    reimportado = ja_existia is not None and ja_existia["inicio"] is not None

    conexao.execute(
        "INSERT INTO sessao (id, unidade, operador, inicio, fim, duracao_s, n_varreduras) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "  unidade = excluded.unidade, operador = excluded.operador, "
        "  inicio = excluded.inicio, fim = excluded.fim, "
        "  duracao_s = excluded.duracao_s, n_varreduras = excluded.n_varreduras",
        (
            arquivo.sessao_id, arquivo.unidade, arquivo.operador,
            arquivo.inicio, arquivo.fim, arquivo.duracao_s, arquivo.n_varreduras,
        ),
    )

    return Resultado(
        arquivo=caminho.name,
        tipo="sessao",
        identificador=arquivo.sessao_id,
        reimportado=reimportado,
    )


def _arquivar(caminho, pasta_brutos):
    """RNF05: o CSV bruto do leitor e preservado como veio."""
    pasta = Path(pasta_brutos)
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / caminho.name
    if destino.resolve() != caminho.resolve():
        shutil.copy2(caminho, destino)
    return destino


def conferir_sessoes(conexao):
    """Sessoes cujo numero de varreduras importadas nao bate com o declarado.

    Pega arquivo que ficou para tras na copia do aparelho, que de outro modo
    viraria um "nao localizado" falso na conciliacao.
    """
    return conexao.execute(
        "SELECT s.id, s.unidade, s.n_varreduras AS declaradas, "
        "       COUNT(v.id) AS importadas "
        "FROM sessao s LEFT JOIN varredura v ON v.sessao_id = s.id "
        "GROUP BY s.id "
        "HAVING s.n_varreduras IS NOT NULL AND s.n_varreduras <> COUNT(v.id)"
    ).fetchall()
