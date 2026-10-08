"""Linha de comando do lado do computador.

    python -m inventario salas <planilha>        cadastro de salas e tags (RF01)
    python -m inventario bens <planilha>         relacao patrimonial (RF02)
    python -m inventario importar <arquivo|pasta>  CSV do leitor (RF06)
    python -m inventario resumo                  o que ja esta no banco
"""

import argparse
import sys
from pathlib import Path

from . import esquema
from .cadastro import (
    bens_sem_sala, importar_bens, importar_salas, salas_com_poucas_tags,
)
from .etiquetagem import (
    EtiquetagemInvalida, bens_sem_etiqueta, importar_etiquetagem, progresso,
)
from .importador import conferir_sessoes, importar_caminho
from .leitor_csv import ArquivoInvalido
from .planilha import PlanilhaInvalida

PADRAO_DB = Path("dados/inventario.db")
PADRAO_BRUTOS = Path("dados/brutos")
TAGS_POR_SALA = 5  # 4 cantos + centro


def main(argv=None):
    analisador = argparse.ArgumentParser(
        prog="inventario",
        description="Cadastro do inventario e importacao dos CSV do leitor RFID.",
    )
    analisador.add_argument(
        "--db", default=PADRAO_DB, type=Path, help=f"banco SQLite (padrao: {PADRAO_DB})")
    comandos = analisador.add_subparsers(dest="comando", required=True)

    salas = comandos.add_parser(
        "salas", help="cadastra salas e suas tags de referencia (RF01)")
    salas.add_argument("caminho", type=Path, help="planilha .csv ou .xlsx")
    salas.add_argument(
        "--minimo-tags", type=int, default=TAGS_POR_SALA,
        help=f"avisa sobre salas com menos tags que isto (padrao: {TAGS_POR_SALA})")

    bens = comandos.add_parser(
        "bens", help="importa a relacao patrimonial (RF02)")
    bens.add_argument("caminho", type=Path, help="planilha .csv ou .xlsx")

    etiq = comandos.add_parser(
        "etiquetagem", help="importa os pares EPC/patrimonio do aparelho (RF03)")
    etiq.add_argument("caminho", type=Path, help="etiquetagem_*.csv do DT50")

    importar = comandos.add_parser(
        "importar", help="importa os CSV de leitura do aparelho (RF06)")
    importar.add_argument("caminho", type=Path, help="arquivo .csv ou pasta com varios")
    importar.add_argument(
        "--brutos", default=PADRAO_BRUTOS, type=Path,
        help=f"pasta onde o CSV original e preservado (padrao: {PADRAO_BRUTOS})")
    importar.add_argument(
        "--sem-arquivar", action="store_true",
        help="nao copia o CSV original para a pasta de brutos")

    comandos.add_parser("resumo", help="mostra o cadastro e as coletas ja importadas")

    args = analisador.parse_args(argv)
    _saida_em_utf8()
    conexao = esquema.conectar(args.db)
    try:
        return {
            "salas": lambda: _salas(args, conexao),
            "bens": lambda: _bens(args, conexao),
            "etiquetagem": lambda: _etiquetagem(args, conexao),
            "importar": lambda: _importar(args, conexao),
            "resumo": lambda: _resumo(conexao),
        }[args.comando]()
    finally:
        conexao.close()


# ------------------------------------------------------------------- RF01

def _salas(args, conexao):
    try:
        relatorio = importar_salas(args.caminho, conexao)
    except PlanilhaInvalida as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1

    print(
        f"{Path(relatorio.arquivo).name}: {relatorio.criados} tags cadastradas, "
        f"{relatorio.atualizados} atualizadas"
    )
    _mostrar_inconsistencias(relatorio)

    salas = conexao.execute(
        "SELECT s.unidade, s.nome, COUNT(t.epc) AS tags "
        "FROM sala s LEFT JOIN tag_referencia t ON t.sala_id = s.id "
        "GROUP BY s.id ORDER BY s.unidade, s.nome"
    ).fetchall()
    print(f"\n{len(salas)} salas cadastradas:")
    for s in salas:
        print(f"  {s['unidade']} / {s['nome']}: {s['tags']} tags")

    faltando = salas_com_poucas_tags(conexao, args.minimo_tags)
    for s in faltando:
        print(
            f"\nATENCAO: {s['unidade']} / {s['nome']} tem {s['tags']} tags de "
            f"referencia; o esperado sao {args.minimo_tags} (4 cantos + centro).",
            file=sys.stderr,
        )
    return 0


# ------------------------------------------------------------------- RF02

def _bens(args, conexao):
    if conexao.execute("SELECT COUNT(*) FROM sala").fetchone()[0] == 0:
        print(
            "ERRO: nenhuma sala cadastrada ainda. Rode 'inventario salas' antes, "
            "senao nenhum bem consegue ser ligado ao seu ambiente.",
            file=sys.stderr,
        )
        return 1

    try:
        relatorio = importar_bens(args.caminho, conexao)
    except PlanilhaInvalida as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1

    print(
        f"{Path(relatorio.arquivo).name}: {relatorio.criados} bens novos, "
        f"{relatorio.atualizados} atualizados"
    )
    _mostrar_inconsistencias(relatorio)

    total = conexao.execute("SELECT COUNT(*) FROM bem").fetchone()[0]
    sem_sala = bens_sem_sala(conexao)
    metalicos = conexao.execute(
        "SELECT COUNT(*) FROM bem WHERE metalico = 1").fetchone()[0]

    print(f"\n{total} bens na base.")
    if metalicos:
        print(f"  {metalicos} marcados como metalicos (precisam de etiqueta on-metal)")
    if sem_sala:
        print(
            f"\nATENCAO: {sem_sala} bens estao sem sala esperada e nunca serao "
            f"conciliados. Cadastre as salas que faltam e reimporte a relacao.",
            file=sys.stderr,
        )
    return 0


# ------------------------------------------------------------------- RF03

def _etiquetagem(args, conexao):
    if conexao.execute("SELECT COUNT(*) FROM bem").fetchone()[0] == 0:
        print(
            "ERRO: a relacao patrimonial ainda nao foi importada. Rode "
            "'inventario bens' antes, senao nao ha a que vincular as etiquetas.",
            file=sys.stderr,
        )
        return 1

    try:
        relatorio = importar_etiquetagem(args.caminho, conexao)
    except EtiquetagemInvalida as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1

    print(
        f"{Path(relatorio.arquivo).name}: {relatorio.vinculados} bens etiquetados, "
        f"{relatorio.revinculados} reetiquetados"
    )
    if relatorio.so_por_qr:
        print(
            f"  {relatorio.so_por_qr} tiveram o EPC capturado pelo QR, nao pelo radio"
        )
    _mostrar_inconsistencias(relatorio)

    com_epc, total = progresso(conexao)
    faltam = total - com_epc
    print()
    print(f"Etiquetagem: {com_epc} de {total} bens ({faltam} faltam)")
    if faltam:
        print("  proximos sem etiqueta:")
        for b in bens_sem_etiqueta(conexao):
            sala = b["sala"] or "sem sala"
            print(f"    {b['patrimonio']}  {b['descricao']}  ({sala})")
    return 0


# ------------------------------------------------------------------- RF06

def _importar(args, conexao):
    try:
        resultados = importar_caminho(
            args.caminho, conexao,
            pasta_brutos=None if args.sem_arquivar else args.brutos,
        )
    except ArquivoInvalido as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1

    if not resultados:
        print("Nenhum arquivo .csv encontrado.")
        return 0

    total_brutas = total_distintas = 0
    for r in resultados:
        marca = " (reimportado)" if r.reimportado else ""
        if r.tipo == "varredura":
            total_brutas += r.linhas_brutas
            total_distintas += r.etiquetas_distintas
            print(
                f"{r.arquivo}: varredura {r.identificador}{marca} | "
                f"{r.linhas_brutas} leituras -> {r.etiquetas_distintas} etiquetas "
                f"({r.linhas_descartadas} repetidas descartadas)"
            )
        else:
            print(f"{r.arquivo}: sessao {r.identificador}{marca}")

    if total_brutas:
        print(
            f"\nTotal: {total_brutas} leituras brutas -> {total_distintas} etiquetas "
            f"({total_brutas - total_distintas} repetidas descartadas)"
        )

    for linha in conferir_sessoes(conexao):
        print(
            f"\nATENCAO: a sessao {linha['id']} ({linha['unidade']}) declara "
            f"{linha['declaradas']} varreduras, mas {linha['importadas']} foram "
            f"importadas. Falta copiar arquivo do aparelho?",
            file=sys.stderr,
        )
    return 0


# ------------------------------------------------------------------ resumo

def _resumo(conexao):
    salas = conexao.execute("SELECT COUNT(*) FROM sala").fetchone()[0]
    tags = conexao.execute("SELECT COUNT(*) FROM tag_referencia").fetchone()[0]
    bens = conexao.execute("SELECT COUNT(*) FROM bem").fetchone()[0]
    print(f"Cadastro: {salas} salas, {tags} tags de referencia, {bens} bens")
    if bens:
        com_epc, total = progresso(conexao)
        print(f"  {com_epc} de {total} bens etiquetados (RF03)")

    sem_sala = bens_sem_sala(conexao) if bens else 0
    if sem_sala:
        print(f"  {sem_sala} bens sem sala esperada")

    sessoes = conexao.execute(
        "SELECT s.id, s.unidade, s.operador, s.duracao_s, COUNT(v.id) AS varreduras "
        "FROM sessao s LEFT JOIN varredura v ON v.sessao_id = s.id "
        "GROUP BY s.id ORDER BY s.id"
    ).fetchall()

    if not sessoes:
        print("\nNenhuma coleta importada ainda.")
        return 0

    for s in sessoes:
        duracao = f"{s['duracao_s']} s" if s["duracao_s"] is not None else "em aberto"
        print(f"\nSessao {s['id']} | {s['unidade']} | {s['operador']} | {duracao}")
        for v in conexao.execute(
            "SELECT v.numero, v.duracao_s, v.linhas_brutas, COUNT(l.epc) AS etiquetas "
            "FROM varredura v LEFT JOIN leitura l ON l.varredura_id = v.id "
            "WHERE v.sessao_id = ? GROUP BY v.id ORDER BY v.numero",
            (s["id"],),
        ):
            duracao_v = f"{v['duracao_s']} s" if v["duracao_s"] is not None else "?"
            print(
                f"  v{v['numero']:02d} | {duracao_v:>7} | "
                f"{v['linhas_brutas']:>6} leituras -> {v['etiquetas']:>4} etiquetas"
            )
    return 0


def _saida_em_utf8():
    """Descricoes e nomes de sala vem com acento da planilha do cliente.

    No Windows o console costuma assumir cp1252 e embaralha o que imprimimos.
    O 'replace' garante que, no pior caso, o relatorio sai com um caractere
    trocado em vez de morrer no meio.
    """
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _mostrar_inconsistencias(relatorio, limite=20):
    """Relatorio de inconsistencias previsto na secao 5.1."""
    if not relatorio.inconsistencias:
        return
    print(f"\n{len(relatorio.inconsistencias)} linhas com problema:", file=sys.stderr)
    for numero_linha, problema in relatorio.inconsistencias[:limite]:
        print(f"  linha {numero_linha}: {problema}", file=sys.stderr)
    restantes = len(relatorio.inconsistencias) - limite
    if restantes > 0:
        print(f"  ... e mais {restantes}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
