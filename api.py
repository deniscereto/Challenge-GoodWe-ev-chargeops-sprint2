"""
API REST do EV ChargeOps (camada de aplicação -> apresentação).

Serve o app do morador e integrações externas. Para subir:

    uvicorn api:app --reload

Documentação interativa (Swagger): http://127.0.0.1:8000/docs
"""

from datetime import date

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from chargeops import anomalias, config, database, pipeline, previsao, rateio

app = FastAPI(
    title="EV ChargeOps API",
    description="Sessões de recarga, rateio por kWh e alertas de IA para recarga compartilhada (GoodWe HCA G2).",
    version="2.0.0",
)


def get_conn():
    """Abre uma conexão por requisição e fecha no final."""
    if not config.CAMINHO_BANCO.exists():
        raise HTTPException(503, "Banco não encontrado. Rode antes: python executar_pipeline.py")
    conn = database.conectar()
    try:
        yield conn
    finally:
        conn.close()


def _lista(linhas) -> list[dict]:
    return [dict(l) for l in linhas]


class Revisao(BaseModel):
    aprovar: bool


class Periodo(BaseModel):
    inicio: date
    fim: date


@app.get("/unidades")
def listar_unidades(conn=Depends(get_conn)):
    return _lista(conn.execute("SELECT * FROM unidades ORDER BY id").fetchall())


@app.get("/sessoes")
def listar_sessoes(ciclo: str | None = None, status: str | None = None, unidade: str | None = None, conn=Depends(get_conn)):
    """Filtros opcionais: ciclo=2026-09, status=em_revisao, unidade=AP-101."""
    sql, params = "SELECT * FROM sessoes WHERE 1=1", []
    if ciclo:
        sql += " AND substr(inicio, 1, 7) = ?"
        params.append(ciclo)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if unidade:
        sql += " AND unidade_id = ?"
        params.append(unidade)
    return _lista(conn.execute(sql + " ORDER BY inicio", params).fetchall())


@app.get("/alertas")
def listar_alertas(conn=Depends(get_conn)):
    """Sessões retidas pela IA aguardando decisão do gestor."""
    return _lista(conn.execute("SELECT * FROM sessoes WHERE status = 'em_revisao' ORDER BY inicio").fetchall())


@app.post("/sessoes/{sessao_id}/revisao")
def revisar(sessao_id: int, revisao: Revisao, conn=Depends(get_conn)):
    """Gestor aprova ou rejeita uma sessão suspeita; a fatura do mês é recalculada na hora."""
    try:
        novo_status = anomalias.revisar_sessao(conn, sessao_id, revisao.aprovar)
    except ValueError as erro:
        raise HTTPException(400, str(erro))
    ciclo = conn.execute("SELECT substr(inicio, 1, 7) FROM sessoes WHERE id = ?", (sessao_id,)).fetchone()[0]
    rateio.calcular_ciclo(conn, ciclo)
    return {"sessao_id": sessao_id, "status": novo_status, "fatura_recalculada": ciclo}


@app.get("/faturas/{ciclo}")
def faturas_do_ciclo(ciclo: str, conn=Depends(get_conn)):
    return _lista(conn.execute("SELECT * FROM faturas WHERE ciclo = ? ORDER BY unidade_id", (ciclo,)).fetchall())


@app.get("/faturas/{ciclo}/{unidade_id}")
def fatura_da_unidade(ciclo: str, unidade_id: str, conn=Depends(get_conn)):
    """Visão do morador: fatura do mês + extrato das sessões."""
    fatura = conn.execute("SELECT * FROM faturas WHERE ciclo = ? AND unidade_id = ?", (ciclo, unidade_id)).fetchone()
    if fatura is None:
        raise HTTPException(404, f"Sem fatura para {unidade_id} em {ciclo} (unidade não usou o carregador)")
    sessoes = conn.execute(
        """SELECT s.id, s.inicio, s.duracao_min, s.kwh, s.status, s.alerta, u.nome AS usuario
           FROM sessoes s LEFT JOIN usuarios u ON u.id = s.usuario_id
           WHERE s.unidade_id = ? AND substr(s.inicio, 1, 7) = ? ORDER BY s.inicio""",
        (unidade_id, ciclo),
    ).fetchall()
    return {"fatura": dict(fatura), "sessoes": _lista(sessoes)}


@app.post("/ingestao")
def executar_ingestao(periodo: Periodo, conn=Depends(get_conn)):
    """Busca novas sessões no SEMS (mock), passa pela IA e recalcula as faturas."""
    resultado = pipeline.executar(conn, periodo.inicio, periodo.fim)
    return {"ingestao": resultado["ingestao"], "ia_anomalias": resultado["ia_anomalias"],
            "ciclos_recalculados": list(resultado["faturas"])}


@app.get("/previsao")
def previsao_consumo(conn=Depends(get_conn)):
    p = previsao.prever(conn)
    return {
        "total_previsto_kwh": p["total_previsto_kwh"],
        "custo_previsto_energia": p["custo_previsto_energia"],
        "tendencia_kwh_por_dia": p["tendencia_kwh_por_dia"],
        "avaliacao": p["avaliacao"],
        "por_unidade": p["por_unidade"].to_dict(orient="records"),
        "diaria": [{"dia": d.date().isoformat(), "kwh_previsto": k} for d, k in p["previsao_diaria"].itertuples(index=False)],
    }
