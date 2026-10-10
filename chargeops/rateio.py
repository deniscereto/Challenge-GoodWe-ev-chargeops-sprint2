"""
Rateio Engine + Billing: transforma as sessões do mês em uma fatura por unidade.

Regra da Sprint 1 (seção 4.3):

    fatura_unidade = Σ (kwh_sessao × tarifa)              [sessões cobráveis da unidade no mês]
                   + custo_manutencao / nº de unidades ativas   (só se a unidade usou no mês)
                   + taxa da plataforma (% sobre a energia, linha separada - seção 4.6)

Casos excepcionais tratados:
  - sessão interrompida            -> cobra só o kWh entregue (vem naturalmente da medição)
  - unidade sem sessão no mês      -> não aparece na fatura, não paga manutenção
  - dois carros na mesma unidade   -> sessões somadas por unidade_id, fatura única
  - sessão suspeita (IA)           -> fica retida (em_revisao), não é cobrada até o gestor aprovar
"""

from datetime import datetime

from chargeops import config

STATUS_COBRAVEIS = ("valida", "aprovada")


def calcular_ciclo(
    conn,
    ciclo: str,
    tarifa: float = config.TARIFA_KWH,
    custo_manutencao: float = config.CUSTO_MANUTENCAO_MENSAL,
    taxa_plataforma_pct: float = config.TAXA_PLATAFORMA_PCT,
) -> list[dict]:
    """Calcula e grava as faturas do ciclo 'AAAA-MM'. Pode ser rodado de novo após revisões."""
    pendentes = conn.execute(
        "SELECT COUNT(*) FROM sessoes WHERE status = 'pendente' AND substr(inicio, 1, 7) = ?", (ciclo,)
    ).fetchone()[0]
    if pendentes:
        # Ponto de integração com a IA: sessão que não foi validada não pode ser cobrada.
        raise RuntimeError(
            f"{pendentes} sessão(ões) de {ciclo} ainda não passaram pela detecção de anomalias"
        )

    linhas = conn.execute(
        f"""
        SELECT unidade_id,
               SUM(CASE WHEN status IN {STATUS_COBRAVEIS} THEN kwh ELSE 0 END) AS kwh_cobrado,
               SUM(CASE WHEN status IN {STATUS_COBRAVEIS} THEN 1 ELSE 0 END)   AS sessoes_cobradas,
               SUM(CASE WHEN status = 'em_revisao' THEN 1 ELSE 0 END)          AS sessoes_em_revisao
        FROM sessoes
        WHERE substr(inicio, 1, 7) = ? AND unidade_id IS NOT NULL
        GROUP BY unidade_id
        ORDER BY unidade_id
        """,
        (ciclo,),
    ).fetchall()

    # "Unidade ativa" = teve pelo menos uma sessão cobrada no mês.
    ativas = [l for l in linhas if l["sessoes_cobradas"] > 0]
    manutencao_por_unidade = custo_manutencao / len(ativas) if ativas else 0.0

    agora = datetime.now().isoformat(timespec="seconds")
    faturas = []
    conn.execute("DELETE FROM faturas WHERE ciclo = ?", (ciclo,))
    for l in linhas:
        kwh = l["kwh_cobrado"]
        valor_energia = round(kwh * tarifa, 2)
        taxa_manutencao = round(manutencao_por_unidade, 2) if l["sessoes_cobradas"] > 0 else 0.0
        taxa_plataforma = round(valor_energia * taxa_plataforma_pct, 2)
        fatura = {
            "unidade_id": l["unidade_id"],
            "ciclo": ciclo,
            "kwh_cobrado": round(kwh, 2),
            "valor_energia": valor_energia,
            "taxa_manutencao": taxa_manutencao,
            "taxa_plataforma": taxa_plataforma,
            "total": round(valor_energia + taxa_manutencao + taxa_plataforma, 2),
            "sessoes_cobradas": l["sessoes_cobradas"],
            "sessoes_em_revisao": l["sessoes_em_revisao"],
            "gerada_em": agora,
        }
        conn.execute(
            """
            INSERT INTO faturas VALUES (:unidade_id, :ciclo, :kwh_cobrado, :valor_energia,
                :taxa_manutencao, :taxa_plataforma, :total, :sessoes_cobradas,
                :sessoes_em_revisao, :gerada_em)
            """,
            fatura,
        )
        faturas.append(fatura)
    conn.commit()
    return faturas


def ciclos_disponiveis(conn) -> list[str]:
    linhas = conn.execute("SELECT DISTINCT substr(inicio, 1, 7) AS ciclo FROM sessoes ORDER BY ciclo").fetchall()
    return [l["ciclo"] for l in linhas]
