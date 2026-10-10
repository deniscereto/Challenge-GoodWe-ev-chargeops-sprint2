"""
AI Engine (segunda frente): previsão de consumo do condomínio.

Pergunta que responde para o gestor: "quanta energia os carregadores vão
consumir nos próximos 30 dias, e quanto disso é de cada unidade?"

Modelo: regressão linear (scikit-learn) sobre o consumo diário, com duas
variáveis:
  - tendência (dias desde o início do histórico): a frota elétrica do prédio cresce;
  - fim de semana (0/1): o padrão de uso muda entre dias úteis e sábado/domingo.

Testamos também uma coluna para cada dia da semana e o número de usuários
ativos; nenhuma das duas melhorou o erro com 3 meses de histórico, então
ficamos com o modelo mais simples (ver README, "Decisões técnicas").

Antes de confiar no modelo, ele é testado nos últimos 14 dias do histórico
(que ele NÃO viu no treino) e comparado com um método ingênuo: "repetir a
média dos últimos 28 dias". Se o modelo não ganhar do método ingênuo, isso
aparece no resultado.
"""

from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error

from chargeops import config


def serie_diaria(conn) -> pd.DataFrame:
    """Consumo diário (kWh) das sessões legítimas. Dias sem recarga entram com zero."""
    df = pd.read_sql_query(
        "SELECT substr(inicio, 1, 10) AS dia, kwh FROM sessoes WHERE status IN ('valida', 'aprovada')",
        conn,
    )
    if df.empty:
        return pd.DataFrame(columns=["dia", "kwh"])
    diario = df.groupby("dia")["kwh"].sum()
    diario.index = pd.to_datetime(diario.index)
    todos_os_dias = pd.date_range(diario.index.min(), diario.index.max(), freq="D")
    diario = diario.reindex(todos_os_dias, fill_value=0.0)
    return diario.rename_axis("dia").reset_index()


def _variaveis(dias: pd.Series, primeiro_dia: pd.Timestamp) -> pd.DataFrame:
    """Transforma uma lista de datas nas variáveis que o modelo usa."""
    return pd.DataFrame({
        "tendencia": (dias - primeiro_dia).dt.days.values,
        "fim_de_semana": (dias.dt.weekday >= 5).astype(int).values,  # sábado=5, domingo=6
    })


def avaliar_modelo(serie: pd.DataFrame, dias_validacao: int = config.DIAS_VALIDACAO) -> dict:
    """Treina sem os últimos N dias e mede o erro neles (MAE em kWh/dia)."""
    treino, teste = serie.iloc[:-dias_validacao], serie.iloc[-dias_validacao:]
    primeiro = serie["dia"].min()
    modelo = LinearRegression().fit(_variaveis(treino["dia"], primeiro), treino["kwh"])
    previsto = np.clip(modelo.predict(_variaveis(teste["dia"], primeiro)), 0, None)
    ingenuo = np.full(len(teste), treino["kwh"].tail(28).mean())
    return {
        "dias_validacao": dias_validacao,
        "mae_modelo_kwh_dia": round(float(mean_absolute_error(teste["kwh"], previsto)), 2),
        "mae_ingenuo_kwh_dia": round(float(mean_absolute_error(teste["kwh"], ingenuo)), 2),
        "real_total_kwh": round(float(teste["kwh"].sum()), 1),
        "previsto_total_kwh": round(float(previsto.sum()), 1),
        "ingenuo_total_kwh": round(float(ingenuo.sum()), 1),
    }


def prever(conn, dias_a_frente: int = config.DIAS_PREVISAO) -> dict:
    """Treina com todo o histórico e projeta os próximos dias."""
    serie = serie_diaria(conn)
    if len(serie) < 28:
        raise ValueError("Histórico curto demais para prever (mínimo de 28 dias)")

    avaliacao = avaliar_modelo(serie)

    primeiro = serie["dia"].min()
    modelo = LinearRegression().fit(_variaveis(serie["dia"], primeiro), serie["kwh"])
    futuro = pd.Series(pd.date_range(serie["dia"].max() + timedelta(days=1), periods=dias_a_frente, freq="D"))
    previsto = np.clip(modelo.predict(_variaveis(futuro, primeiro)), 0, None)
    previsao_diaria = pd.DataFrame({"dia": futuro, "kwh_previsto": previsto.round(2)})

    total_previsto = float(previsto.sum())
    return {
        "historico": serie,
        "previsao_diaria": previsao_diaria,
        "total_previsto_kwh": round(total_previsto, 1),
        "custo_previsto_energia": round(total_previsto * config.TARIFA_KWH, 2),
        "por_unidade": _dividir_por_unidade(conn, total_previsto),
        "avaliacao": avaliacao,
        "tendencia_kwh_por_dia": round(float(modelo.coef_[0]), 3),
    }


def _dividir_por_unidade(conn, total_previsto: float) -> pd.DataFrame:
    """
    Distribui a previsão do condomínio entre as unidades conforme a participação
    de cada uma nos últimos 30 dias (quem mais usou recentemente, mais deve usar).
    """
    df = pd.read_sql_query(
        """
        SELECT unidade_id, SUM(kwh) AS kwh_30d
        FROM sessoes
        WHERE status IN ('valida', 'aprovada')
          AND date(inicio) > date((SELECT MAX(inicio) FROM sessoes), '-30 days')
        GROUP BY unidade_id
        """,
        conn,
    )
    df["participacao"] = df["kwh_30d"] / df["kwh_30d"].sum()
    df["kwh_previsto"] = (df["participacao"] * total_previsto).round(1)
    df["fatura_estimada"] = (
        df["kwh_previsto"] * config.TARIFA_KWH * (1 + config.TAXA_PLATAFORMA_PCT)
        + config.CUSTO_MANUTENCAO_MENSAL / len(df)
    ).round(2)
    return df.sort_values("kwh_previsto", ascending=False).reset_index(drop=True)
