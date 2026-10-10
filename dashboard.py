"""
Dashboard do gestor (camada de apresentação), em Streamlit.

    streamlit run dashboard.py

Se o banco ainda não existir, ele é criado na primeira abertura com os dados
simulados (mesmo resultado de `python executar_pipeline.py`).
"""

import altair as alt
import pandas as pd
import streamlit as st

from chargeops import anomalias, config, database, pipeline, previsao, rateio, simulador

AZUL = "#2a78d6"      # série principal (consumo)
LARANJA = "#eb6834"   # segunda série (previsão)
VERMELHO = "#d03b3b"  # estado crítico: sessão retida pela IA
CINZA = "#8a8984"

st.set_page_config(page_title="EV ChargeOps", page_icon="⚡", layout="wide")


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------
def inicializar_se_preciso() -> None:
    if config.CAMINHO_BANCO.exists():
        return
    with st.spinner("Primeira execução: simulando o carregador e processando as sessões..."):
        simulador.gerar_historico()
        conn = database.conectar()
        pipeline.preparar_banco(conn)
        pipeline.executar(conn, simulador.INICIO_PADRAO, simulador.FIM_PADRAO)
        conn.close()


def ler(sql: str, params=()) -> pd.DataFrame:
    conn = database.conectar()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def brl_md(valor: float) -> str:
    """Versão para st.markdown: o '$' sozinho é lido como fórmula matemática."""
    return brl(valor).replace("$", "\\$")


def grafico_base(chart: alt.Chart) -> alt.Chart:
    """Eixos e grade discretos, para o dado aparecer mais que a moldura."""
    return chart.configure_axis(gridColor="#e4e3df", domainColor=CINZA, tickColor=CINZA, labelFontSize=12,
                                titleFontSize=12, titleFontWeight="normal").configure_view(strokeWidth=0)


inicializar_se_preciso()

# ---------------------------------------------------------------------------
# Barra lateral
# ---------------------------------------------------------------------------
conn = database.conectar()
ciclos = rateio.ciclos_disponiveis(conn)
conn.close()

with st.sidebar:
    st.header("EV ChargeOps")
    st.caption("Condomínio piloto · 2 carregadores GoodWe HCA G2 (7 kW)")
    ciclo = st.selectbox("Ciclo de faturamento", ciclos, index=len(ciclos) - 1)
    st.divider()
    st.markdown("**Regras de rateio vigentes**")
    st.markdown(
        f"- Tarifa: {brl_md(config.TARIFA_KWH)}/kWh\n"
        f"- Manutenção: {brl_md(config.CUSTO_MANUTENCAO_MENSAL)}/mês, dividida entre unidades ativas\n"
        f"- Taxa da plataforma: {config.TAXA_PLATAFORMA_PCT:.0%} sobre a energia"
    )
    st.divider()
    if st.button("Recriar dados simulados", help="Apaga o banco e processa de novo (desfaz as revisões)."):
        config.CAMINHO_BANCO.unlink(missing_ok=True)
        st.rerun()

# ---------------------------------------------------------------------------
# Indicadores do ciclo
# ---------------------------------------------------------------------------
faturas = ler("SELECT * FROM faturas WHERE ciclo = ? ORDER BY unidade_id", (ciclo,))
sessoes = ler(
    """SELECT s.*, u.nome AS usuario FROM sessoes s LEFT JOIN usuarios u ON u.id = s.usuario_id
       WHERE substr(s.inicio, 1, 7) = ? ORDER BY s.inicio""",
    (ciclo,),
)
retidas = sessoes[sessoes["status"] == "em_revisao"]

st.title("Painel do gestor")
st.caption(f"Ciclo {ciclo} · sessões ingeridas da API SEMS (simulada), validadas pela IA e rateadas por kWh")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Energia cobrada", f"{faturas['kwh_cobrado'].sum():,.0f} kWh".replace(",", "."))
k2.metric("Total a arrecadar", brl(faturas["total"].sum()))
k3.metric("Sessões no mês", len(sessoes))
k4.metric("Retidas pela IA", len(retidas), help="Sessões suspeitas fora da cobrança até você revisar")
k5.metric("Unidades ativas", int((faturas["sessoes_cobradas"] > 0).sum()), help="Só elas dividem a manutenção")

aba_faturas, aba_alertas, aba_uso, aba_previsao, aba_morador = st.tabs(
    ["Faturas", f"Alertas da IA ({len(retidas)})", "Padrão de uso", "Previsão de consumo", "Visão do morador"]
)

# ---------------------------------------------------------------------------
# Faturas
# ---------------------------------------------------------------------------
with aba_faturas:
    col_graf, col_tab = st.columns([2, 3])
    with col_graf:
        st.subheader("Total por unidade")
        barras = (
            alt.Chart(faturas)
            .mark_bar(color=AZUL, cornerRadiusEnd=4, size=18)
            .encode(
                x=alt.X("total:Q", title="R$"),
                y=alt.Y("unidade_id:N", sort="-x", title=None),
                tooltip=[alt.Tooltip("unidade_id", title="Unidade"), alt.Tooltip("kwh_cobrado", title="kWh"),
                         alt.Tooltip("total", title="Total (R$)", format=",.2f")],
            )
            .properties(height=max(220, 30 * len(faturas)))
        )
        st.altair_chart(grafico_base(barras), width="stretch")
    with col_tab:
        st.subheader("Fatura detalhada")
        tabela = faturas.rename(columns={
            "unidade_id": "Unidade", "kwh_cobrado": "kWh", "valor_energia": "Energia (R$)",
            "taxa_manutencao": "Manutenção (R$)", "taxa_plataforma": "Plataforma (R$)", "total": "Total (R$)",
            "sessoes_cobradas": "Sessões", "sessoes_em_revisao": "Retidas",
        }).drop(columns=["ciclo", "gerada_em"])
        st.dataframe(tabela, hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="%.2f")
                                    for c in ["kWh", "Energia (R$)", "Manutenção (R$)", "Plataforma (R$)", "Total (R$)"]})
        unidades = ler("SELECT id FROM unidades")["id"]
        sem_uso = sorted(set(unidades) - set(faturas["unidade_id"]))
        if sem_uso:
            st.caption(f"Sem uso no mês (não pagam nada): {', '.join(sem_uso)}")

# ---------------------------------------------------------------------------
# Alertas da IA
# ---------------------------------------------------------------------------
with aba_alertas:
    st.subheader("Sessões retidas aguardando sua decisão")
    st.caption("Aprovar inclui a sessão na fatura; rejeitar cancela a cobrança. A fatura é recalculada na hora.")
    if retidas.empty:
        st.success("Nenhuma sessão pendente de revisão neste ciclo.")
    for _, s in retidas.iterrows():
        with st.container(border=True):
            c1, c2, c3 = st.columns([6, 1, 1])
            quem = f"{s['unidade_id']} · {s['usuario']}" if pd.notna(s["unidade_id"]) else f"Cartão {s['cartao_rfid']} (não cadastrado)"
            c1.markdown(
                f"**#{s['id']} · {quem}** · {pd.to_datetime(s['inicio']):%d/%m %H:%M} · "
                f"{s['duracao_min']:.0f} min · {s['kwh']:.1f} kWh · {s['potencia_media_kw']:.1f} kW"
            )
            for motivo in str(s["alerta"]).split("; "):
                c1.markdown(f"⚠️ {motivo}")
            if c2.button("Aprovar", key=f"ok{s['id']}", disabled=pd.isna(s["unidade_id"])):
                conn = database.conectar()
                anomalias.revisar_sessao(conn, int(s["id"]), aprovar=True)
                rateio.calcular_ciclo(conn, ciclo)
                conn.close()
                st.rerun()
            if c3.button("Rejeitar", key=f"no{s['id']}"):
                conn = database.conectar()
                anomalias.revisar_sessao(conn, int(s["id"]), aprovar=False)
                rateio.calcular_ciclo(conn, ciclo)
                conn.close()
                st.rerun()

    st.subheader("Como a IA enxerga as sessões do mês")
    st.caption("Cada ponto é uma sessão. As retidas ficam fora da nuvem de sessões comuns: muito longas para pouca energia, energia demais, ou zero.")
    dispersao = sessoes.assign(
        situacao=sessoes["status"].map({"em_revisao": "Retida pela IA", "rejeitada": "Rejeitada"}).fillna("Cobrada"),
        horas=sessoes["duracao_min"] / 60,
    )
    pontos = (
        alt.Chart(dispersao)
        .mark_point(filled=True, size=110, opacity=0.9)
        .encode(
            x=alt.X("horas:Q", title="Duração (h)"),
            y=alt.Y("kwh:Q", title="Energia (kWh)", scale=alt.Scale(domain=[0, float(dispersao["kwh"].max()) * 1.1])),
            color=alt.Color("situacao:N", title=None, scale=alt.Scale(
                domain=["Cobrada", "Retida pela IA", "Rejeitada"], range=[AZUL, VERMELHO, CINZA]),
                legend=alt.Legend(orient="top")),
            shape=alt.Shape("situacao:N", title=None, scale=alt.Scale(
                domain=["Cobrada", "Retida pela IA", "Rejeitada"], range=["circle", "triangle-up", "cross"]), legend=None),
            tooltip=[alt.Tooltip("id", title="Sessão"), alt.Tooltip("unidade_id", title="Unidade"),
                     alt.Tooltip("horas", title="Horas", format=".1f"), alt.Tooltip("kwh", title="kWh", format=".1f"),
                     alt.Tooltip("potencia_media_kw", title="kW médio", format=".1f"), alt.Tooltip("situacao", title="Situação")],
        )
        .properties(height=360)
    )
    st.altair_chart(grafico_base(pontos), width="stretch")

    decididas = ler(
        "SELECT id, unidade_id, inicio, kwh, status, revisado_em, alerta FROM sessoes "
        "WHERE status IN ('aprovada', 'rejeitada') ORDER BY revisado_em DESC"
    )
    if not decididas.empty:
        st.subheader("Histórico de revisões")
        st.dataframe(decididas, hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Padrão de uso
# ---------------------------------------------------------------------------
with aba_uso:
    st.subheader("Quando os carregadores são usados")
    st.caption("Energia entregue por dia da semana e hora de início, no ciclo selecionado. Ajuda a ver picos e ociosidade.")
    validas = sessoes[sessoes["status"].isin(["valida", "aprovada"])].copy()
    inicio = pd.to_datetime(validas["inicio"])
    dias_pt = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
    validas["dia_semana"] = inicio.dt.weekday.map(dict(enumerate(dias_pt)))
    validas["hora"] = inicio.dt.hour
    mapa = (
        alt.Chart(validas)
        .mark_rect(cornerRadius=2, stroke="white", strokeWidth=2)
        .encode(
            x=alt.X("hora:O", title="Hora de início", scale=alt.Scale(domain=list(range(24)))),
            y=alt.Y("dia_semana:O", title=None, sort=dias_pt),
            color=alt.Color("sum(kwh):Q", title="kWh", scale=alt.Scale(range=["#cde2fb", "#104281"])),
            tooltip=[alt.Tooltip("dia_semana", title="Dia"), alt.Tooltip("hora", title="Hora"),
                     alt.Tooltip("sum(kwh):Q", title="kWh", format=".1f"), alt.Tooltip("count():Q", title="Sessões")],
        )
        .properties(height=260)
    )
    st.altair_chart(grafico_base(mapa), width="stretch")

    # Ocupação: horas com carro plugado / horas disponíveis nos 2 carregadores.
    dias_no_mes = pd.Period(ciclo).days_in_month
    horas_disponiveis = dias_no_mes * 24 * len(simulador.CARREGADORES)
    ocupacao = sessoes["duracao_min"].sum() / 60 / horas_disponiveis
    c1, c2 = st.columns(2)
    c1.metric("Ocupação dos carregadores", f"{ocupacao:.0%}", help="Horas com carro plugado ÷ horas disponíveis no mês")
    c2.metric("Energia média por sessão", f"{validas['kwh'].mean():.1f} kWh")

# ---------------------------------------------------------------------------
# Previsão
# ---------------------------------------------------------------------------
with aba_previsao:
    conn = database.conectar()
    p = previsao.prever(conn)
    conn.close()
    av = p["avaliacao"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Consumo previsto (30 dias)", f"{p['total_previsto_kwh']:,.0f} kWh".replace(",", "."))
    c2.metric("Custo de energia previsto", brl(p["custo_previsto_energia"]))
    c3.metric("Erro médio na validação", f"{av['mae_modelo_kwh_dia']:.1f} kWh/dia",
              delta=f"{av['mae_modelo_kwh_dia'] - av['mae_ingenuo_kwh_dia']:+.1f} vs. média simples",
              delta_color="inverse",
              help=f"Testado nos últimos {av['dias_validacao']} dias, que o modelo não viu no treino.")

    # Junta realizado + previsto numa série só e aplica média móvel de 7 dias
    # (o dado diário oscila muito). O último dia realizado é repetido na série
    # prevista para as duas linhas se encontrarem no gráfico.
    serie = pd.concat([
        p["historico"].rename(columns={"kwh": "kWh"}).assign(serie="Realizado"),
        p["previsao_diaria"].rename(columns={"kwh_previsto": "kWh"}).assign(serie="Previsto"),
    ], ignore_index=True)
    serie["kWh"] = serie["kWh"].rolling(7, min_periods=1).mean()
    emenda = serie[serie["serie"] == "Realizado"].tail(1).assign(serie="Previsto")
    linhas = (
        alt.Chart(pd.concat([serie, emenda]))
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m")),
            y=alt.Y("kWh:Q", title="kWh/dia (média de 7 dias)"),
            color=alt.Color("serie:N", title=None, scale=alt.Scale(domain=["Realizado", "Previsto"], range=[AZUL, LARANJA]),
                            legend=alt.Legend(orient="top")),
            strokeDash=alt.StrokeDash("serie:N", scale=alt.Scale(domain=["Realizado", "Previsto"], range=[[1, 0], [6, 4]]), legend=None),
            tooltip=[alt.Tooltip("dia:T", title="Dia", format="%d/%m"), alt.Tooltip("kWh:Q", format=".1f"), alt.Tooltip("serie", title="Série")],
        )
        .properties(height=320)
    )
    st.altair_chart(grafico_base(linhas), width="stretch")

    st.subheader("Estimativa por unidade no próximo ciclo")
    st.caption("A previsão do condomínio é dividida pela participação de cada unidade nos últimos 30 dias.")
    por_unidade = p["por_unidade"].assign(participacao=p["por_unidade"]["participacao"] * 100)
    st.dataframe(
        por_unidade.rename(columns={"unidade_id": "Unidade", "kwh_30d": "kWh últimos 30 dias",
                                         "participacao": "Participação", "kwh_previsto": "kWh previsto",
                                         "fatura_estimada": "Fatura estimada (R$)"}),
        hide_index=True, width="stretch",
        column_config={"Participação": st.column_config.NumberColumn(format="%.1f%%"),
                       "kWh últimos 30 dias": st.column_config.NumberColumn(format="%.1f"),
                       "kWh previsto": st.column_config.NumberColumn(format="%.1f"),
                       "Fatura estimada (R$)": st.column_config.NumberColumn(format="%.2f")},
    )

# ---------------------------------------------------------------------------
# Visão do morador
# ---------------------------------------------------------------------------
with aba_morador:
    st.caption("O que o morador veria no app: a fatura da unidade e o extrato de cada recarga.")
    unidade = st.selectbox("Unidade", sorted(faturas["unidade_id"]))
    f = faturas[faturas["unidade_id"] == unidade].iloc[0]
    c1, c2 = st.columns([1, 2])
    with c1:
        with st.container(border=True):
            st.markdown(f"#### Fatura {unidade} · {ciclo}")
            st.markdown(
                f"Energia ({f['kwh_cobrado']:.1f} kWh × {brl_md(config.TARIFA_KWH)}): **{brl_md(f['valor_energia'])}**  \n"
                f"Manutenção (rateada): **{brl_md(f['taxa_manutencao'])}**  \n"
                f"Taxa da plataforma: **{brl_md(f['taxa_plataforma'])}**"
            )
            st.markdown(f"### Total: {brl_md(f['total'])}")
            if f["sessoes_em_revisao"]:
                st.info(f"{int(f['sessoes_em_revisao'])} recarga(s) em análise ainda não entraram nesta fatura.")
    with c2:
        extrato = sessoes[sessoes["unidade_id"] == unidade][["inicio", "usuario", "duracao_min", "kwh", "status"]]
        extrato = extrato.assign(
            inicio=pd.to_datetime(extrato["inicio"]).dt.strftime("%d/%m %H:%M"),
            status=extrato["status"].map({"valida": "Cobrada", "aprovada": "Cobrada (revisada)",
                                          "em_revisao": "Em análise", "rejeitada": "Cancelada"}),
        ).rename(columns={"inicio": "Início", "usuario": "Usuário", "duracao_min": "Minutos", "kwh": "kWh", "status": "Situação"})
        st.dataframe(extrato, hide_index=True, width="stretch",
                     column_config={"Minutos": st.column_config.NumberColumn(format="%.0f")})
