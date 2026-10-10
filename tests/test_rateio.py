"""
Testes do Rateio Engine: um teste para cada caso excepcional da Sprint 1 (seção 4.3).

    python -m pytest -v
"""

import pytest

from chargeops import anomalias, database, rateio

TARIFA = 1.00
MANUTENCAO = 100.00


@pytest.fixture
def conn():
    c = database.conectar(":memory:")
    database.criar_tabelas(c)
    database.cadastrar_carregador(c, "CHG-1", "GoodWe HCA G2 7kW", 7.0, "teste")
    for apto in ("AP-1", "AP-2", "AP-3"):
        database.cadastrar_unidade(c, apto, "A", f"Morador {apto}")
    database.cadastrar_usuario(c, "Ana", "CARD-ANA", "AP-1", "Carro 1")
    database.cadastrar_usuario(c, "Beto", "CARD-BETO", "AP-1", "Carro 2")  # 2º carro da AP-1
    database.cadastrar_usuario(c, "Caio", "CARD-CAIO", "AP-2", "Carro 3")
    database.cadastrar_usuario(c, "Duda", "CARD-DUDA", "AP-3", "Carro 4")  # não usa em setembro
    c.commit()
    return c


_contador = 0


def inserir_sessao(conn, unidade, kwh, status="valida", inicio="2026-09-10T20:00:00", usuario_id=1):
    global _contador
    _contador += 1
    conn.execute(
        """
        INSERT INTO sessoes (id_externo, carregador_id, cartao_rfid, usuario_id, unidade_id, inicio, fim,
                             duracao_min, kwh, potencia_media_kw, potencia_pico_kw, motivo_fim, status, ingerido_em)
        VALUES (?, 'CHG-1', 'X', ?, ?, ?, ?, 180, ?, 6.0, 6.5, 'EV_FULL', ?, '2026-10-01')
        """,
        (f"T{_contador}", usuario_id, unidade, inicio, inicio, kwh, status),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def faturas_por_unidade(conn):
    faturas = rateio.calcular_ciclo(conn, "2026-09", tarifa=TARIFA, custo_manutencao=MANUTENCAO, taxa_plataforma_pct=0.0)
    return {f["unidade_id"]: f for f in faturas}


def test_cobra_kwh_vezes_tarifa_mais_manutencao(conn):
    inserir_sessao(conn, "AP-2", 20.0)
    f = faturas_por_unidade(conn)["AP-2"]
    assert f["valor_energia"] == 20.0
    assert f["taxa_manutencao"] == 100.0  # única unidade ativa paga a manutenção inteira
    assert f["total"] == 120.0


def test_sessao_interrompida_cobra_so_o_que_foi_entregue(conn):
    inserir_sessao(conn, "AP-2", 3.4)  # morador tirou o carro cedo: só 3,4 kWh
    assert faturas_por_unidade(conn)["AP-2"]["valor_energia"] == 3.4


def test_unidade_sem_sessao_nao_paga_nada(conn):
    inserir_sessao(conn, "AP-1", 10.0)
    inserir_sessao(conn, "AP-2", 10.0)
    faturas = faturas_por_unidade(conn)
    assert "AP-3" not in faturas
    # manutenção dividida só entre as duas unidades ativas
    assert faturas["AP-1"]["taxa_manutencao"] == 50.0
    assert faturas["AP-2"]["taxa_manutencao"] == 50.0


def test_dois_carros_mesma_unidade_somam_numa_fatura(conn):
    inserir_sessao(conn, "AP-1", 12.0, usuario_id=1)
    inserir_sessao(conn, "AP-1", 8.0, usuario_id=2)
    f = faturas_por_unidade(conn)["AP-1"]
    assert f["kwh_cobrado"] == 20.0
    assert f["sessoes_cobradas"] == 2


def test_sessao_em_revisao_fica_fora_da_cobranca(conn):
    inserir_sessao(conn, "AP-1", 10.0)
    inserir_sessao(conn, "AP-1", 90.0, status="em_revisao")
    f = faturas_por_unidade(conn)["AP-1"]
    assert f["kwh_cobrado"] == 10.0
    assert f["sessoes_em_revisao"] == 1


def test_sessao_aprovada_pelo_gestor_entra_na_proxima_apuracao(conn):
    inserir_sessao(conn, "AP-1", 10.0)
    suspeita = inserir_sessao(conn, "AP-1", 30.0, status="em_revisao")
    assert faturas_por_unidade(conn)["AP-1"]["kwh_cobrado"] == 10.0

    anomalias.revisar_sessao(conn, suspeita, aprovar=True)
    assert faturas_por_unidade(conn)["AP-1"]["kwh_cobrado"] == 40.0


def test_sessao_rejeitada_nunca_e_cobrada(conn):
    inserir_sessao(conn, "AP-1", 10.0)
    suspeita = inserir_sessao(conn, "AP-1", 30.0, status="em_revisao")
    anomalias.revisar_sessao(conn, suspeita, aprovar=False)
    assert faturas_por_unidade(conn)["AP-1"]["kwh_cobrado"] == 10.0


def test_rateio_bloqueado_se_ia_nao_rodou(conn):
    inserir_sessao(conn, "AP-1", 10.0, status="pendente")
    with pytest.raises(RuntimeError, match="detecção de anomalias"):
        faturas_por_unidade(conn)


def test_unidade_so_com_sessao_retida_nao_paga_manutencao(conn):
    inserir_sessao(conn, "AP-1", 10.0)
    inserir_sessao(conn, "AP-2", 50.0, status="em_revisao")
    faturas = faturas_por_unidade(conn)
    assert faturas["AP-2"]["total"] == 0.0
    assert faturas["AP-1"]["taxa_manutencao"] == 100.0


def test_taxa_da_plataforma_aparece_separada(conn):
    inserir_sessao(conn, "AP-2", 100.0)
    f = rateio.calcular_ciclo(conn, "2026-09", tarifa=1.0, custo_manutencao=0.0, taxa_plataforma_pct=0.06)[0]
    assert f["valor_energia"] == 100.0
    assert f["taxa_plataforma"] == 6.0
    assert f["total"] == 106.0
