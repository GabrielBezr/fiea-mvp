"""Cadastro do administrador: salas com tags de referencia (RF01) e relacao
patrimonial (RF02).

Os dois importadores seguem a mitigacao prevista na secao 5.1 para relacao
desatualizada ou heterogenea: nunca recusam o arquivo inteiro por causa de
linhas ruins. As linhas boas entram, as ruins viram um relatorio de
inconsistencias para o administrador corrigir e reimportar.
"""

from dataclasses import dataclass, field
from datetime import datetime

from .planilha import PlanilhaInvalida, ler_linhas, mapear_colunas, valor

COLUNAS_SALAS = {
    "unidade": (["unidade", "predio", "campus"], True),
    "sala": (["sala", "nome", "ambiente", "local", "laboratorio"], True),
    "posicao": (["posicao", "ponto", "canto"], True),
    "epc": (["epc", "tag", "etiqueta", "tag de referencia"], True),
}

COLUNAS_BENS = {
    "patrimonio": (
        ["patrimonio", "n patrimonio", "no patrimonio", "numero de patrimonio",
         "numero do patrimonio", "tombo", "n tombo"], True),
    "descricao": (["descricao", "bem", "descricao do bem", "item", "especificacao"], True),
    "sala_esperada": (
        ["sala esperada", "sala", "localizacao", "local", "ambiente", "setor"], True),
    "unidade": (["unidade", "predio", "campus"], False),
    "metalico": (["metalico", "metal", "sobre metal", "e metalico"], False),
}

VERDADEIROS = {"1", "s", "sim", "true", "v", "verdadeiro", "x", "y", "yes"}


@dataclass
class Relatorio:
    """Resultado de um cadastro, com o que entrou e o que precisa de conserto."""
    arquivo: str
    criados: int = 0
    atualizados: int = 0
    inconsistencias: list = field(default_factory=list)

    @property
    def aceitos(self):
        return self.criados + self.atualizados

    def anotar(self, numero_linha, problema):
        self.inconsistencias.append((numero_linha, problema))


# ------------------------------------------------------------------- RF01

def importar_salas(caminho, conexao):
    """Uma linha por tag de referencia; a sala nasce da combinacao unidade+nome."""
    cabecalho, linhas = ler_linhas(caminho)
    colunas = mapear_colunas(cabecalho, COLUNAS_SALAS, caminho)
    relatorio = Relatorio(arquivo=str(caminho))

    vistos = {}
    for numero_linha, linha in enumerate(linhas, start=2):
        unidade = valor(linha, colunas["unidade"])
        nome = valor(linha, colunas["sala"])
        posicao = valor(linha, colunas["posicao"]).lower()
        epc = valor(linha, colunas["epc"]).upper()

        if not any([unidade, nome, posicao, epc]):
            continue  # linha em branco no fim da planilha
        if not (unidade and nome and posicao and epc):
            relatorio.anotar(numero_linha, "unidade, sala, posicao e epc sao obrigatorios")
            continue
        if epc in vistos:
            relatorio.anotar(
                numero_linha, f"epc {epc} repetido (ja usado na linha {vistos[epc]})")
            continue
        vistos[epc] = numero_linha

        sala_id = _sala_id(conexao, unidade, nome)
        dono = conexao.execute(
            "SELECT sala_id, posicao FROM tag_referencia WHERE epc = ?", (epc,)
        ).fetchone()
        if dono is not None and dono["sala_id"] != sala_id:
            # Uma tag so pode pertencer a uma sala: aceitar silenciosamente
            # mandaria a varredura inteira para o ambiente errado.
            relatorio.anotar(
                numero_linha,
                f"epc {epc} ja cadastrado em outra sala; remova de la antes de mover")
            continue

        ocupante = conexao.execute(
            "SELECT epc FROM tag_referencia WHERE sala_id = ? AND posicao = ?",
            (sala_id, posicao),
        ).fetchone()
        if ocupante is not None and ocupante["epc"] != epc:
            relatorio.anotar(
                numero_linha,
                f"a posicao '{posicao}' de {nome} ja tem a tag {ocupante['epc']}")
            continue

        if dono is None:
            conexao.execute(
                "INSERT INTO tag_referencia (epc, sala_id, posicao) VALUES (?, ?, ?)",
                (epc, sala_id, posicao))
            relatorio.criados += 1
        else:
            conexao.execute(
                "UPDATE tag_referencia SET sala_id = ?, posicao = ? WHERE epc = ?",
                (sala_id, posicao, epc))
            relatorio.atualizados += 1

    conexao.commit()
    return relatorio


def salas_com_poucas_tags(conexao, minimo=5):
    """Salas abaixo do esperado (4 cantos + centro).

    Nao e erro de cadastro, e aviso de etiquetagem incompleta: quanto menos tags,
    pior fica depois decidir se uma etiqueta esta mesmo naquela sala.
    """
    return conexao.execute(
        "SELECT s.unidade, s.nome, COUNT(t.epc) AS tags "
        "FROM sala s LEFT JOIN tag_referencia t ON t.sala_id = s.id "
        "GROUP BY s.id HAVING COUNT(t.epc) < ? ORDER BY s.unidade, s.nome",
        (minimo,),
    ).fetchall()


# ------------------------------------------------------------------- RF02

def importar_bens(caminho, conexao):
    """Relacao patrimonial: patrimonio, descricao e sala esperada de cada bem."""
    cabecalho, linhas = ler_linhas(caminho)
    colunas = mapear_colunas(cabecalho, COLUNAS_BENS, caminho)
    relatorio = Relatorio(arquivo=str(caminho))
    agora = datetime.now().isoformat(timespec="seconds")

    unidade_unica = _unidade_unica(conexao)
    vistos = {}
    for numero_linha, linha in enumerate(linhas, start=2):
        patrimonio = valor(linha, colunas["patrimonio"])
        descricao = valor(linha, colunas["descricao"])
        nome_sala = valor(linha, colunas["sala_esperada"])
        unidade = valor(linha, colunas["unidade"]) or unidade_unica

        if not any([patrimonio, descricao, nome_sala]):
            continue
        if not patrimonio:
            relatorio.anotar(numero_linha, "sem numero de patrimonio")
            continue
        if patrimonio in vistos:
            relatorio.anotar(
                numero_linha,
                f"patrimonio {patrimonio} repetido (ja na linha {vistos[patrimonio]})")
            continue
        vistos[patrimonio] = numero_linha

        sala_id = None
        if not nome_sala:
            relatorio.anotar(numero_linha, f"{patrimonio}: sem sala esperada")
        elif not unidade:
            relatorio.anotar(
                numero_linha,
                f"{patrimonio}: ha mais de uma unidade cadastrada e a planilha "
                f"nao diz a qual '{nome_sala}' pertence")
        else:
            sala = conexao.execute(
                "SELECT id FROM sala WHERE unidade = ? AND nome = ?", (unidade, nome_sala)
            ).fetchone()
            if sala is None:
                # O bem entra assim mesmo: a sala pode ser cadastrada depois e a
                # reimportacao resolve. Mas sem isso ele nunca sera conciliado.
                relatorio.anotar(
                    numero_linha,
                    f"{patrimonio}: sala '{nome_sala}' nao esta cadastrada (RF01)")
            else:
                sala_id = sala["id"]

        metalico = _booleano(valor(linha, colunas["metalico"]))
        existente = conexao.execute(
            "SELECT patrimonio FROM bem WHERE patrimonio = ?", (patrimonio,)
        ).fetchone()

        # Reimportar a relacao nunca apaga epc nem metalico: sao fruto da
        # etiquetagem (RF03) e custam uma campanha de campo para refazer.
        conexao.execute(
            "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, metalico, importado_em) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(patrimonio) DO UPDATE SET "
            "  descricao = excluded.descricao, "
            "  sala_esperada_id = excluded.sala_esperada_id, "
            "  metalico = COALESCE(excluded.metalico, bem.metalico), "
            "  importado_em = excluded.importado_em",
            (patrimonio, descricao, sala_id, metalico, agora),
        )
        if existente is None:
            relatorio.criados += 1
        else:
            relatorio.atualizados += 1

    conexao.commit()
    return relatorio


def bens_sem_sala(conexao):
    """Bens que nunca poderao ser conciliados enquanto a sala nao existir."""
    return conexao.execute(
        "SELECT COUNT(*) FROM bem WHERE sala_esperada_id IS NULL").fetchone()[0]


# ------------------------------------------------------------------ comuns

def _sala_id(conexao, unidade, nome):
    conexao.execute(
        "INSERT OR IGNORE INTO sala (unidade, nome) VALUES (?, ?)", (unidade, nome))
    return conexao.execute(
        "SELECT id FROM sala WHERE unidade = ? AND nome = ?", (unidade, nome)
    ).fetchone()["id"]


def _unidade_unica(conexao):
    """Se so existe uma unidade cadastrada, a planilha de bens nao precisa dize-la."""
    unidades = conexao.execute("SELECT DISTINCT unidade FROM sala").fetchall()
    return unidades[0]["unidade"] if len(unidades) == 1 else ""


def _booleano(texto):
    if not texto:
        return None
    return 1 if texto.strip().lower() in VERDADEIROS else 0
