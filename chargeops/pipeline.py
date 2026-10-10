"""
Orquestra o fluxo da Sprint 1 (seção 4.2), na ordem:

    SEMS (mock) -> ingestão/normalização -> IA de anomalias -> rateio -> faturas
"""

from datetime import date

from chargeops import anomalias, database, ingestao, rateio, simulador
from chargeops.sems_client import ClienteSemsMock


def preparar_banco(conn) -> None:
    database.criar_tabelas(conn)
    simulador.cadastrar_condominio(conn)


def executar(conn, inicio: date, fim: date, cliente=None) -> dict:
    cliente = cliente or ClienteSemsMock()
    resultado = {"ingestao": ingestao.ingerir_periodo(conn, cliente, inicio, fim)}
    resultado["ia_anomalias"] = anomalias.analisar_pendentes(conn)
    resultado["faturas"] = {ciclo: rateio.calcular_ciclo(conn, ciclo) for ciclo in rateio.ciclos_disponiveis(conn)}
    return resultado
