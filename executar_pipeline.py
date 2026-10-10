"""
Executa o EV ChargeOps de ponta a ponta pelo terminal.

    python executar_pipeline.py            # gera dados simulados e processa tudo
    python executar_pipeline.py --manter   # reprocessa sem apagar o banco atual

Ao final mostra: resumo da ingestão, alertas da IA, faturas de cada mês e a
previsão de consumo para os próximos 30 dias.
"""

import argparse
import json

from chargeops import config, database, pipeline, previsao, simulador


def linha(titulo: str) -> None:
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")


def main() -> None:
    parser = argparse.ArgumentParser(description="EV ChargeOps - pipeline completo")
    parser.add_argument("--manter", action="store_true", help="não apaga o banco nem regera os dados")
    args = parser.parse_args()

    if not args.manter:
        config.CAMINHO_BANCO.unlink(missing_ok=True)
        linha("1. Simulando o carregador HCA G2 / API SEMS")
        info = simulador.gerar_historico()
        print(f"Sessões geradas: {info['sessoes']}  |  arquivos JSON em {config.PASTA_MOCK_SEMS.relative_to(config.RAIZ_PROJETO)}")

    conn = database.conectar()
    pipeline.preparar_banco(conn)
    resultado = pipeline.executar(conn, simulador.INICIO_PADRAO, simulador.FIM_PADRAO)

    linha("2. Ingestão (Session Service)")
    print(json.dumps(resultado["ingestao"], indent=2, ensure_ascii=False))

    linha("3. IA - detecção de anomalias (antes do rateio)")
    ia = resultado["ia_anomalias"]
    print(f"Analisadas: {ia['analisadas']}  |  válidas: {ia['validas']}  |  retidas para revisão: {ia['em_revisao']}")
    retidas = conn.execute(
        "SELECT id, inicio, unidade_id, kwh, alerta FROM sessoes WHERE status = 'em_revisao' ORDER BY inicio"
    ).fetchall()
    for s in retidas:
        print(f"  #{s['id']:<4} {s['inicio'][:16]}  {s['unidade_id'] or 'SEM UNIDADE':<11} {s['kwh']:>6.1f} kWh  ->  {s['alerta']}")

    linha("4. Rateio - faturas por unidade")
    for ciclo, faturas in resultado["faturas"].items():
        total = sum(f["total"] for f in faturas)
        print(f"\nCiclo {ciclo}  (tarifa R$ {config.TARIFA_KWH:.2f}/kWh, manutenção R$ {config.CUSTO_MANUTENCAO_MENSAL:.2f})")
        print(f"  {'Unidade':<8} {'kWh':>8} {'Energia':>10} {'Manut.':>8} {'Plataf.':>8} {'TOTAL':>10} {'Sessões':>8} {'Retidas':>8}")
        for f in faturas:
            print(
                f"  {f['unidade_id']:<8} {f['kwh_cobrado']:>8.1f} {f['valor_energia']:>10.2f} {f['taxa_manutencao']:>8.2f} "
                f"{f['taxa_plataforma']:>8.2f} {f['total']:>10.2f} {f['sessoes_cobradas']:>8} {f['sessoes_em_revisao']:>8}"
            )
        print(f"  {'Total arrecadado no ciclo:':<48} R$ {total:>10.2f}")

    linha("5. IA - previsão de consumo (próximos 30 dias)")
    p = previsao.prever(conn)
    av = p["avaliacao"]
    print(f"Consumo previsto: {p['total_previsto_kwh']} kWh  (≈ R$ {p['custo_previsto_energia']:.2f} em energia)")
    print(f"Tendência: {p['tendencia_kwh_por_dia']:+.3f} kWh/dia a cada dia que passa")
    print(
        f"Validação nos últimos {av['dias_validacao']} dias: erro médio do modelo {av['mae_modelo_kwh_dia']} kWh/dia "
        f"vs. {av['mae_ingenuo_kwh_dia']} kWh/dia do método ingênuo"
    )
    print(p["por_unidade"].to_string(index=False))


if __name__ == "__main__":
    main()
