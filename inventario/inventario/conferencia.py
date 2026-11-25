"""Confronta os bens cadastrados com as leituras de uma sessao (RF07/RF08)."""

from dataclasses import dataclass, field


LOCALIZADO = "localizado"
NAO_LOCALIZADO = "nao localizado"
FORA_DO_LOCAL = "fora do local"
SEM_COBERTURA = "sem cobertura"
NAO_CADASTRADO = "nao cadastrado"
# Lido em mais de uma sala sem como dizer em qual esta: o operador precisa procurar.
INDETERMINADO = "indeterminado"

# Sala provavel de um bem lido em mais de uma sala (salas vizinhas). Valores iniciais,
# a calibrar na bancada com duas salas vizinhas de verdade.
MARGEM_SINAL = 6               # diferenca minima, na unidade do RSSI do coletor
MIN_LEITURAS_SINAL = 2         # uma leitura so pode ser um pico de sorte
RSSI_MAIOR_E_MAIS_FORTE = True  # no DT50 o numero cai quando a etiqueta se afasta
METRICA_SINAL = "max"          # "max" (pico da varredura) ou "medio"

# Como a sala provavel foi decidida (BemConferido.criterio).
CRITERIO_CONFIRMADO = "confirmado"  # QR ou manual do operador em Pendencias
CRITERIO_UNICA = "unica"            # lido numa sala so
CRITERIO_SINAL = "sinal"            # sinal claramente mais forte numa das salas
CRITERIO_CADASTRO = "cadastro"      # empate no sinal; vale a sala do cadastro
CRITERIO_EMPATE = "empate"          # empate e o cadastro nao e de nenhuma delas
CRITERIO_SALA_NAO_VARRIDA = "sala nao varrida"  # lido fora, sala esperada sem varredura

# leitura.metodo: alem do radio (RFID), as resolucoes de Pendencias que o coletor
# envia. QR e manual sao o operador confirmando o bem na sala; faltante e o operador
# dizendo que procurou ali e nao achou.
METODOS_CONFIRMACAO = {"QR", "MANUAL"}
METODO_FALTANTE = "FALTANTE"
# Metodos pelos quais um bem e dado como localizado (BemConferido.metodo).
METODO_RFID = "RFID"
METODO_QR = "QR"
METODO_MANUAL = "MANUAL"
# RF13: meta de bens localizados pelo radio, em % dos localizados.
META_RFID_PCT = 90.0

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
    sala_provavel: str = ""
    criterio: str = ""
    # Como a sala provavel foi estabelecida: RFID, QR ou MANUAL ("" se nao ha sala).
    metodo: str = ""
    # RSSI da METRICA_SINAL na melhor varredura de cada sala, como o coletor mede.
    sinal_por_sala: dict = field(default_factory=dict)


@dataclass
class EpcNaoCadastrado:
    epc: str
    salas_lidas: list = field(default_factory=list)
    sala_provavel: str = ""
    criterio: str = ""
    sinal_por_sala: dict = field(default_factory=dict)


@dataclass
class _Sinal:
    valor: float          # RSSI como o coletor mede
    forca: float          # o mesmo valor, com maior = mais forte em qualquer convencao
    configuracao: tuple   # (tecnica, potencia_dbm): so se compara o que foi varrido igual


def _sinal(leitura, varredura):
    """Sinal da etiqueta numa varredura, ou None se nao serve para comparar salas."""
    if leitura["leituras"] < MIN_LEITURAS_SINAL:
        return None
    rssi_min, rssi_max = leitura["rssi_min"], leitura["rssi_max"]
    # Sem RSSI, ou RSSI zerado das sincronizacoes antigas pelo Wi-Fi.
    if rssi_min is None or rssi_max is None or (rssi_min == 0 and rssi_max == 0):
        return None
    if METRICA_SINAL == "medio":
        valor = leitura["rssi_medio"]
        if valor is None:
            return None
    else:
        valor = rssi_max if RSSI_MAIOR_E_MAIS_FORTE else rssi_min
    forca = valor if RSSI_MAIOR_E_MAIS_FORTE else -valor
    return _Sinal(valor, forca, (varredura["tecnica"], varredura["potencia_dbm"]))


def _decidir_sala(salas, confirmadas, sala_esperada_id=None):
    """Escolhe a sala do bem entre as salas onde ele apareceu na sessao.

    salas: {sala_id: _Sinal ou None}; confirmadas: salas com QR ou manual.
    Devolve (sala_id ou None, criterio).
    """
    if confirmadas:
        if sala_esperada_id in confirmadas:
            return sala_esperada_id, CRITERIO_CONFIRMADO
        if len(confirmadas) == 1:
            return next(iter(confirmadas)), CRITERIO_CONFIRMADO
        return None, CRITERIO_EMPATE
    if not salas:
        return None, ""
    if len(salas) == 1:
        return next(iter(salas)), CRITERIO_UNICA
    # Comparar so faz sentido com sinal em todas as salas, medido do mesmo jeito.
    sinais = list(salas.items())
    if all(s is not None for _, s in sinais) and len({s.configuracao for _, s in sinais}) == 1:
        sinais.sort(key=lambda item: item[1].forca, reverse=True)
        if sinais[0][1].forca - sinais[1][1].forca >= MARGEM_SINAL:
            return sinais[0][0], CRITERIO_SINAL
    if sala_esperada_id in salas:
        return sala_esperada_id, CRITERIO_CADASTRO
    return None, CRITERIO_EMPATE


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
        "SELECT v.id, v.sala_id, s.nome AS sala, v.tecnica, v.potencia_dbm "
        "FROM varredura v LEFT JOIN sala s ON s.id = v.sala_id "
        "WHERE v.sessao_id = ? ORDER BY v.numero",
        (sessao_id,),
    ).fetchall()
    salas_varridas = {v["sala_id"] for v in varreduras if v["sala_id"] is not None}
    nomes_salas = {v["sala_id"]: v["sala"] for v in varreduras if v["sala_id"] is not None}
    # Radio: epc -> {sala_id: melhor _Sinal da etiqueta naquela sala, ou None}
    leituras_por_epc = {}
    confirmados = {}  # QR ou manual: chave do bem -> {sala_id: metodos} onde o operador o achou
    faltantes = {}    # chave do bem -> salas onde o operador procurou e nao achou
    epcs_lidos = set()
    epcs_em_sala_nao_identificada = set()
    for varredura in varreduras:
        epcs = conexao.execute(
            "SELECT epc, metodo, leituras, rssi_min, rssi_medio, rssi_max "
            "FROM leitura WHERE varredura_id = ?",
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
                    confirmados.setdefault(epc, {}).setdefault(
                        varredura["sala_id"], set()).add(metodo)
                continue
            epcs_lidos.add(epc)
            if varredura["sala_id"] is None:
                epcs_em_sala_nao_identificada.add(epc)
            else:
                # Sala varrida mais de uma vez: vale o sinal mais forte entre elas.
                salas = leituras_por_epc.setdefault(epc, {})
                sinal = _sinal(leitura, varredura)
                anterior = salas.get(varredura["sala_id"])
                if varredura["sala_id"] not in salas or (
                        sinal is not None and (anterior is None or sinal.forca > anterior.forca)):
                    salas[varredura["sala_id"]] = sinal

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
        salas = {
            sala_id: sinal for sala_id, sinal in leituras_por_epc.get(epc, {}).items()
            if sala_id not in excluidas
        } if epc else {}
        # A confirmacao do operador vale mais que o faltante marcado na mesma sala.
        metodos_confirmados = {}
        for c in chaves:
            for sala_id, metodos in confirmados.get(c, {}).items():
                metodos_confirmados.setdefault(sala_id, set()).update(metodos)
        confirmadas = set(metodos_confirmados)
        for sala_id in confirmadas:
            salas.setdefault(sala_id, None)
        sala_esperada_id = bem["sala_esperada_id"]
        sala_id, criterio = _decidir_sala(salas, confirmadas, sala_esperada_id)

        if sala_esperada_id is None:
            situacao = SEM_COBERTURA
        elif not salas:
            situacao = NAO_LOCALIZADO if sala_esperada_id in salas_varridas else SEM_COBERTURA
        elif sala_id == sala_esperada_id:
            situacao = LOCALIZADO
        elif sala_esperada_id not in salas_varridas and criterio != CRITERIO_CONFIRMADO:
            # Lido so fora, e a sala dele nem foi varrida: pode ser so o sinal dela
            # atravessando a parede. Sem comparar com ela, nao da para dizer; e o que
            # resolve e varrer a sala, por isso esse motivo vem antes do empate.
            situacao, sala_id, criterio = INDETERMINADO, None, CRITERIO_SALA_NAO_VARRIDA
        elif sala_id is None:
            situacao = INDETERMINADO
        else:
            situacao = FORA_DO_LOCAL

        relatorio.bens.append(BemConferido(
            patrimonio=bem["patrimonio"],
            descricao=bem["descricao"] or "",
            epc=epc,
            sala_esperada=bem["sala_esperada"] or "",
            situacao=situacao,
            salas_lidas=sorted(nomes_salas[s] for s in salas),
            sala_provavel=nomes_salas.get(sala_id, "") if sala_id is not None else "",
            criterio=criterio,
            metodo=_metodo_da_localizacao(sala_id, criterio, metodos_confirmados),
            sinal_por_sala=_sinais_por_nome(salas, nomes_salas),
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
        salas = leituras_por_epc.get(epc, {})
        salas_lidas = sorted(nomes_salas[s] for s in salas)
        if epc in epcs_em_sala_nao_identificada:
            salas_lidas.append("sala nao identificada")
        # Sem cadastro para desempatar: so a sala unica ou o sinal decidem.
        sala_id, criterio = _decidir_sala(salas, set())
        relatorio.epcs_nao_cadastrados.append(EpcNaoCadastrado(
            epc=epc,
            salas_lidas=salas_lidas,
            sala_provavel=nomes_salas.get(sala_id, "") if sala_id is not None else "",
            criterio=criterio,
            sinal_por_sala=_sinais_por_nome(salas, nomes_salas),
        ))

    return relatorio


def _metodo_da_localizacao(sala_id, criterio, metodos_confirmados):
    """RFID, QR ou MANUAL: o que estabeleceu a sala do bem ("" se nao ha sala)."""
    if sala_id is None:
        return ""
    if criterio == CRITERIO_CONFIRMADO:
        # QR vale mais que manual: leu a etiqueta fisica do bem.
        return METODO_QR if METODO_QR in metodos_confirmados.get(sala_id, ()) else METODO_MANUAL
    return METODO_RFID


@dataclass
class ResumoMetodos:
    """RF13: de que jeito os bens localizados foram confirmados."""
    total_bens: int = 0
    localizados: int = 0
    rfid: int = 0
    qr: int = 0
    manual: int = 0
    meta_rfid_pct: float = META_RFID_PCT

    def _pct(self, parte):
        return round(parte / self.localizados * 100, 1) if self.localizados else 0.0

    @property
    def pct_rfid(self):
        return self._pct(self.rfid)

    @property
    def pct_qr(self):
        return self._pct(self.qr)

    @property
    def pct_manual(self):
        return self._pct(self.manual)

    @property
    def atingiu_meta(self):
        return self.localizados > 0 and self.pct_rfid >= self.meta_rfid_pct


def resumir_metodos(bens):
    """Conta os bens LOCALIZADOS por metodo. O percentual e sobre os localizados."""
    resumo = ResumoMetodos(total_bens=len(bens))
    for bem in bens:
        if bem.situacao != LOCALIZADO:
            continue
        resumo.localizados += 1
        if bem.metodo == METODO_QR:
            resumo.qr += 1
        elif bem.metodo == METODO_MANUAL:
            resumo.manual += 1
        else:
            resumo.rfid += 1
    return resumo


def _sinais_por_nome(salas, nomes_salas):
    return {
        nomes_salas[sala_id]: sinal.valor
        for sala_id, sinal in sorted(salas.items(), key=lambda item: nomes_salas[item[0]])
        if sinal is not None
    }
