"""
Mede se a detecção de anomalias funciona.

Como o simulador sabe quais sessões receberam problemas de propósito (o
"gabarito"), dá para calcular:
  - recall    : das anomalias injetadas, quantas a IA reteve?
  - precisão  : das sessões que a IA reteve, quantas eram mesmo anomalias?
  - falsos positivos: sessões normais retidas à toa (o gestor terá que revisar)

Para não depender da sorte de um único histórico, roda em 10 cenários
diferentes (sementes 1 a 10) e mostra o resultado de cada um e a média.
Também mostra o que cada camada (regras, z-score, Isolation Forest) encontrou.

    python avaliar_ia.py
"""

import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from chargeops import anomalias, database, ingestao, simulador
from chargeops.sems_client import ClienteSemsMock

SEMENTES = range(1, 11)


def camada(alerta: str) -> set[str]:
    """Descobre quais camadas da IA dispararam, pelo texto do alerta."""
    alerta = alerta or ""
    achou = set()
    if "Cartão RFID" in alerta or "sem energia" in alerta or "Potência média" in alerta:
        achou.add("regras_fisicas")
    if "z-score" in alerta:
        achou.add("zscore")
    if "Isolation Forest" in alerta:
        achou.add("isolation_forest")
    return achou


def avaliar_cenario(semente: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        simulador.gerar_historico(semente=semente, pasta=tmp / "sems", caminho_gabarito=tmp / "gab.json")
        gabarito = json.loads((tmp / "gab.json").read_text())

        conn = database.conectar(":memory:")
        database.criar_tabelas(conn)
        simulador.cadastrar_condominio(conn)
        ingestao.ingerir_periodo(conn, ClienteSemsMock(tmp / "sems"), simulador.INICIO_PADRAO, simulador.FIM_PADRAO)
        anomalias.analisar_pendentes(conn)
        sessoes = conn.execute("SELECT id_externo, status, alerta FROM sessoes").fetchall()

    retidas = {s["id_externo"] for s in sessoes if s["status"] == "em_revisao"}
    injetadas = set(gabarito)
    vp = retidas & injetadas
    acerto_por_tipo = Counter(gabarito[i] for i in vp)
    total_por_tipo = Counter(gabarito.values())
    camadas = defaultdict(Counter)
    for s in sessoes:
        if s["id_externo"] in vp:
            for c in camada(s["alerta"]):
                camadas[gabarito[s["id_externo"]]][c] += 1
    return {
        "semente": semente,
        "sessoes": len(sessoes),
        "injetadas": len(injetadas),
        "retidas": len(retidas),
        "verdadeiros_positivos": len(vp),
        "falsos_positivos": len(retidas - injetadas),
        "nao_detectadas": len(injetadas - retidas),
        "acerto_por_tipo": acerto_por_tipo,
        "total_por_tipo": total_por_tipo,
        "camadas": camadas,
    }


def main() -> None:
    resultados = [avaliar_cenario(s) for s in SEMENTES]

    print("AVALIAÇÃO DA DETECÇÃO DE ANOMALIAS - 10 cenários simulados (jul-set/2026)\n")
    print(f"{'Semente':>7} {'Sessões':>8} {'Injetadas':>10} {'Retidas':>8} {'Acertos':>8} {'Falso+':>7} {'Perdidas':>9} {'Recall':>7} {'Precisão':>9}")
    for r in resultados:
        recall = r["verdadeiros_positivos"] / r["injetadas"] if r["injetadas"] else 1
        precisao = r["verdadeiros_positivos"] / r["retidas"] if r["retidas"] else 1
        print(
            f"{r['semente']:>7} {r['sessoes']:>8} {r['injetadas']:>10} {r['retidas']:>8} {r['verdadeiros_positivos']:>8} "
            f"{r['falsos_positivos']:>7} {r['nao_detectadas']:>9} {recall:>7.0%} {precisao:>9.0%}"
        )

    tot = {k: sum(r[k] for r in resultados) for k in ("sessoes", "injetadas", "retidas", "verdadeiros_positivos", "falsos_positivos", "nao_detectadas")}
    print(
        f"{'TOTAL':>7} {tot['sessoes']:>8} {tot['injetadas']:>10} {tot['retidas']:>8} {tot['verdadeiros_positivos']:>8} "
        f"{tot['falsos_positivos']:>7} {tot['nao_detectadas']:>9} "
        f"{tot['verdadeiros_positivos'] / tot['injetadas']:>7.0%} {tot['verdadeiros_positivos'] / tot['retidas']:>9.0%}"
    )
    print(f"\nTaxa de falso positivo: {tot['falsos_positivos'] / (tot['sessoes'] - tot['injetadas']):.1%} das sessões normais foram retidas para revisão.")

    acerto, total = Counter(), Counter()
    camadas = defaultdict(Counter)
    for r in resultados:
        acerto.update(r["acerto_por_tipo"])
        total.update(r["total_por_tipo"])
        for tipo, c in r["camadas"].items():
            camadas[tipo].update(c)

    print("\nDetecção por tipo de anomalia e camada da IA que disparou:")
    print(f"  {'Tipo':<22} {'Detectadas':>11}   {'Regras':>7} {'Z-score':>8} {'IsolationF':>11}")
    for tipo in sorted(total):
        c = camadas[tipo]
        print(
            f"  {tipo:<22} {acerto[tipo]:>4}/{total[tipo]:<6}   {c['regras_fisicas']:>7} {c['zscore']:>8} {c['isolation_forest']:>11}"
        )
    print("\n(Uma mesma sessão pode ser pega por mais de uma camada.)")


if __name__ == "__main__":
    main()
