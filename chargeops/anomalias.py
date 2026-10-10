"""
AI Engine: detecção de anomalias nas sessões de recarga.

Papel estrutural (Sprint 1, seção 4.2): toda sessão nova entra como 'pendente'
e o Rateio Engine se recusa a cobrar sessões pendentes. Só depois de passar por
aqui ela vira 'valida' (entra na fatura) ou 'em_revisao' (fica retida até o
gestor aprovar ou rejeitar). Ou seja: sem a IA, ninguém é cobrado.

A análise é feita em três camadas, da mais simples para a mais "inteligente":

1. Regras físicas  - coisas impossíveis ou inconsistentes (kWh zero em sessão
                     longa, potência acima do limite do carregador, cartão
                     desconhecido). Não precisam de modelo: são certezas.
2. Z-score         - compara o consumo da sessão com o histórico da PRÓPRIA
                     unidade. Pega "consumo 3x maior que o normal desse apê".
3. Isolation Forest - modelo não supervisionado (scikit-learn) que olha
                     duração, energia e potência AO MESMO TEMPO. Pega sessões
                     que parecem normais em cada número isolado, mas cuja
                     combinação é rara (ex.: 12 horas plugado a 0,8 kW).
"""

from datetime import datetime

import pandas as pd
from sklearn.ensemble import IsolationForest

from chargeops import config

COLUNAS_MODELO = ["duracao_min", "kwh", "potencia_media_kw"]


def _carregar_sessoes(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT s.*, c.potencia_nominal_kw
        FROM sessoes s
        JOIN carregadores c ON c.id = s.carregador_id
        """,
        conn,
    )


def regras_fisicas(sessao: pd.Series) -> list[str]:
    """Camada 1: verifica inconsistências que não dependem de histórico."""
    motivos = []
    if pd.isna(sessao["unidade_id"]):
        motivos.append(f"Cartão RFID {sessao['cartao_rfid']} não está cadastrado em nenhuma unidade")
    if sessao["kwh"] < config.KWH_MINIMO_VALIDO and sessao["duracao_min"] > config.DURACAO_LIMIAR_SEM_ENERGIA:
        motivos.append(
            f"Sessão de {sessao['duracao_min']:.0f} min sem energia registrada (possível falha do equipamento)"
        )
    limite = sessao["potencia_nominal_kw"] * config.FOLGA_POTENCIA
    if sessao["potencia_media_kw"] > limite:
        motivos.append(
            f"Potência média de {sessao['potencia_media_kw']:.1f} kW acima do máximo do carregador "
            f"({sessao['potencia_nominal_kw']:.0f} kW): provável erro de medição"
        )
    return motivos


def zscore_consumo(sessao: pd.Series, historico_unidade: pd.DataFrame) -> float | None:
    """
    Camada 2: quantos desvios-padrão o kWh da sessão está acima da média da unidade.
    O histórico NÃO inclui a própria sessão (senão ela "puxaria" a média para cima).
    Retorna None se a unidade ainda tem poucas sessões para comparar.
    """
    outras = historico_unidade[historico_unidade["id"] != sessao["id"]]["kwh"]
    if len(outras) < config.MIN_SESSOES_PARA_ZSCORE or outras.std() == 0:
        return None
    return (sessao["kwh"] - outras.mean()) / outras.std()


def treinar_isolation_forest(sessoes: pd.DataFrame) -> IsolationForest:
    """
    Camada 3: treina o modelo com o histórico do condomínio.
    Sessões já reprovadas pelas regras físicas ficam de fora do treino,
    para o modelo aprender como é uma sessão "comum" sem lixo de medição.
    """
    modelo = IsolationForest(
        n_estimators=200,
        contamination=config.CONTAMINACAO_ISOLATION_FOREST,
        random_state=config.SEMENTE_ALEATORIA,
    )
    modelo.fit(sessoes[COLUNAS_MODELO])
    return modelo


def analisar_pendentes(conn) -> dict:
    """Roda as três camadas em todas as sessões 'pendente' e grava o resultado."""
    df = _carregar_sessoes(conn)
    pendentes = df[df["status"] == "pendente"]
    if pendentes.empty:
        return {"analisadas": 0, "validas": 0, "em_revisao": 0}

    # Separa o que as regras físicas já reprovam (não entra no treino nem no z-score).
    df["motivos_fisicos"] = df.apply(regras_fisicas, axis=1)
    limpas = df[(df["motivos_fisicos"].str.len() == 0) & (df["status"] != "rejeitada")]

    modelo = treinar_isolation_forest(limpas)
    # decision_function: positivo = normal, negativo = anômalo (quanto menor, mais estranho)
    df["score"] = modelo.decision_function(df[COLUNAS_MODELO])
    df["isolation_forest"] = modelo.predict(df[COLUNAS_MODELO])  # 1 normal, -1 anômala

    resumo = {"analisadas": 0, "validas": 0, "em_revisao": 0}
    for _, sessao in df[df["status"] == "pendente"].iterrows():
        motivos = list(sessao["motivos_fisicos"])

        if not motivos:  # só faz sentido comparar estatisticamente sessões fisicamente coerentes
            historico = limpas[limpas["unidade_id"] == sessao["unidade_id"]]
            z = zscore_consumo(sessao, historico)
            if z is not None and z > config.LIMIAR_ZSCORE:
                media = historico[historico["id"] != sessao["id"]]["kwh"].mean()
                motivos.append(
                    f"Consumo de {sessao['kwh']:.1f} kWh, {sessao['kwh'] / media:.1f}x a média da unidade "
                    f"({media:.1f} kWh, z-score {z:.1f})"
                )
            if sessao["isolation_forest"] == -1:
                motivos.append(
                    f"Combinação incomum de duração ({sessao['duracao_min']:.0f} min), energia "
                    f"({sessao['kwh']:.1f} kWh) e potência ({sessao['potencia_media_kw']:.1f} kW) "
                    f"segundo o Isolation Forest (score {sessao['score']:.3f})"
                )

        status = "em_revisao" if motivos else "valida"
        conn.execute(
            "UPDATE sessoes SET status = ?, score_anomalia = ?, alerta = ? WHERE id = ?",
            (status, round(float(sessao["score"]), 4), "; ".join(motivos) or None, int(sessao["id"])),
        )
        resumo["analisadas"] += 1
        resumo["validas" if status == "valida" else "em_revisao"] += 1

    conn.commit()
    return resumo


def revisar_sessao(conn, sessao_id: int, aprovar: bool) -> str:
    """Decisão do gestor sobre uma sessão retida pela IA."""
    linha = conn.execute("SELECT status, unidade_id FROM sessoes WHERE id = ?", (sessao_id,)).fetchone()
    if linha is None:
        raise ValueError(f"Sessão {sessao_id} não existe")
    if linha["status"] != "em_revisao":
        raise ValueError(f"Sessão {sessao_id} não está em revisão (status atual: {linha['status']})")
    if aprovar and linha["unidade_id"] is None:
        # Não dá para cobrar uma sessão que não pertence a nenhuma unidade.
        raise ValueError("Cartão não cadastrado: vincule o cartão a uma unidade antes de aprovar a cobrança")
    novo_status = "aprovada" if aprovar else "rejeitada"
    conn.execute(
        "UPDATE sessoes SET status = ?, revisado_em = ? WHERE id = ?",
        (novo_status, datetime.now().isoformat(timespec="seconds"), sessao_id),
    )
    conn.commit()
    return novo_status
