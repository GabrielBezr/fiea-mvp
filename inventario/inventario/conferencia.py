"""Confronta os bens cadastrados com as leituras de uma sessao (RF07/RF08)."""

from dataclasses import dataclass, field


LOCALIZADO = "localizado"
NAO_LOCALIZADO = "nao localizado"
FORA_DO_LOCAL = "fora do local"
SEM_COBERTURA = "sem cobertura"
NAO_CADASTRADO = "nao cadastrado"

# leitura.metodo: alem do radio (RFID), as resolucoes de Pendencias que o coletor
# envia. QR e manual sao o operador confirmando o bem na sala; faltante e o operador
# dizendo que procurou ali e nao achou.
METODOS_CONFIRMACAO = {"QR", "MANUAL"}
METODO_FALTANTE = "FALTANTE"
# Resolucao manual de bem sem EPC: o servidor grava "PAT_<patrimonio>" no lugar do EPC.
PREFIXO_SEM_EPC = "PAT_"


class SessaoNaoEncontrada(Exception):
    """A sessao solicitada nao existe no banco."""


@dataclass
class BemConferido:
    patrimonio: str
    descricao: str
    epc: str
    sala_esperada: str
    situacao: str
    salas_lidas: list = field(default_factory=list)


@dataclass
class EpcNaoCadastrado:
    epc: str
    salas_lidas: list = field(default_factory=list)


@dataclass
class RelatorioConferencia:
    sessao_id: str
    unidade: str
    bens: list = field(default_factory=list)
    epcs_nao_cadastrados: list = field(default_factory=list)


def conferir_sessao(conexao, sessao_id):
    """Classifica bens e EPCs observados nas varreduras de uma sessao."""
    sessao = conexao.execute(
        "SELECT id, unidade FROM sessao WHERE id = ?", (sessao_id,)
    ).fetchone()
    if sessao is None:
        raise SessaoNaoEncontrada(f"sessao '{sessao_id}' nao encontrada")

    varreduras = conexao.execute(
        "SELECT v.id, v.sala_id, s.nome AS sala "
        "FROM varredura v LEFT JOIN sala s ON s.id = v.sala_id "
        "WHERE v.sessao_id = ? ORDER BY v.numero",
        (sessao_id,),
    ).fetchall()
    salas_varridas = {v["sala_id"] for v in varreduras if v["sala_id"] is not None}
    leituras_por_epc = {}
    confirmados = {}  # QR ou manual: chave do bem -> salas onde o operador o achou
    faltantes = {}    # chave do bem -> salas onde o operador procurou e nao achou
    epcs_lidos = set()
    epcs_em_sala_nao_identificada = set()
    for varredura in varreduras:
        epcs = conexao.execute(
            "SELECT DISTINCT epc, metodo FROM leitura WHERE varredura_id = ?",
            (varredura["id"],),
        )
        for leitura in epcs:
            epc = leitura["epc"].strip().upper()
            if not epc:
                continue
            metodo = (leitura["metodo"] or "RFID").strip().upper()
            # Resolucao sem sala conhecida nao diz nada sobre onde o bem esta.
            if metodo == METODO_FALTANTE:
                if varredura["sala_id"] is not None:
                    faltantes.setdefault(epc, set()).add(varredura["sala_id"])
                continue
            if metodo in METODOS_CONFIRMACAO:
                if varredura["sala_id"] is not None:
                    confirmados.setdefault(epc, set()).add(
                        (varredura["sala_id"], varredura["sala"])
                    )
                continue
            epcs_lidos.add(epc)
            if varredura["sala_id"] is None:
                epcs_em_sala_nao_identificada.add(epc)
            else:
                leituras_por_epc.setdefault(epc, set()).add(
                    (varredura["sala_id"], varredura["sala"])
                )

    relatorio = RelatorioConferencia(sessao["id"], sessao["unidade"])
    bens = conexao.execute(
        "SELECT b.patrimonio, b.descricao, b.epc, b.sala_esperada_id, "
        "s.nome AS sala_esperada "
        "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
        "WHERE s.unidade = ? "
        "ORDER BY s.nome, b.patrimonio",
        (sessao["unidade"],),
    ).fetchall()

    for bem in bens:
        epc = (bem["epc"] or "").strip().upper()
        # A resolucao pode ter sido gravada pelo EPC ou, para bem que ainda nao tinha
        # EPC quando foi resolvido, por "PAT_<patrimonio>".
        chaves = {f"{PREFIXO_SEM_EPC}{bem['patrimonio']}".upper()}
        if epc:
            chaves.add(epc)
        excluidas = set().union(*(faltantes.get(c, set()) for c in chaves))
        locais = {
            local for local in leituras_por_epc.get(epc, set()) if local[0] not in excluidas
        } if epc else set()
        # A confirmacao do operador vale mais que o faltante marcado na mesma sala.
        locais |= set().union(*(confirmados.get(c, set()) for c in chaves))
        locais = sorted(locais, key=lambda local: local[1])
        ids_locais = {local[0] for local in locais}
        sala_esperada_id = bem["sala_esperada_id"]

        if sala_esperada_id is None:
            situacao = SEM_COBERTURA
        elif sala_esperada_id in ids_locais:
            situacao = LOCALIZADO
        elif ids_locais:
            situacao = FORA_DO_LOCAL
        elif sala_esperada_id in salas_varridas:
            situacao = NAO_LOCALIZADO
        else:
            situacao = SEM_COBERTURA

        relatorio.bens.append(BemConferido(
            patrimonio=bem["patrimonio"],
            descricao=bem["descricao"] or "",
            epc=epc,
            sala_esperada=bem["sala_esperada"] or "",
            situacao=situacao,
            salas_lidas=[local[1] for local in locais],
        ))

    epcs_cadastrados = {bem["epc"].strip().upper() for bem in bens if bem["epc"]}
    epcs_referencia = {
        (tag["epc"] or "").strip().upper()
        for tag in conexao.execute(
            "SELECT t.epc FROM tag_referencia t "
            "JOIN sala s ON s.id = t.sala_id WHERE s.unidade = ?",
            (sessao["unidade"],),
        )
    }
    epcs_nao_cadastrados = sorted(
        epc for epc in epcs_lidos
        if epc and epc not in epcs_cadastrados and epc not in epcs_referencia
    )
    for epc in epcs_nao_cadastrados:
        locais = sorted(leituras_por_epc.get(epc, set()), key=lambda local: local[1])
        salas_lidas = [local[1] for local in locais]
        if epc in epcs_em_sala_nao_identificada:
            salas_lidas.append("sala nao identificada")
        relatorio.epcs_nao_cadastrados.append(EpcNaoCadastrado(
            epc=epc,
            salas_lidas=salas_lidas,
        ))

    return relatorio
