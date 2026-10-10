"""Testes da ingestão (normalização) e das camadas da detecção de anomalias."""

from datetime import date

import pandas as pd
import pytest

from chargeops import anomalias, database, ingestao

REGISTRO_SEMS = {
    "orderId": "EVO1",
    "sn": "CHG-1",
    "cardNo": "CARD-ANA",
    "startTime": "2026-09-10 20:00:00",
    "endTime": "2026-09-10 23:00:00",
    "chargeDuration": 10800,   # segundos
    "chargeEnergy": 18.0,      # kWh
    "maxPower": 6500,          # W
    "stopReason": "EV_FULL",
}


def test_normalizacao_converte_unidades_e_identifica_morador():
    s = ingestao.normalizar(REGISTRO_SEMS, {"CARD-ANA": (1, "AP-1")})
    assert s["duracao_min"] == 180
    assert s["potencia_pico_kw"] == 6.5
    assert s["potencia_media_kw"] == 6.0      # 18 kWh / 3 h, calculada por nós
    assert s["inicio"] == "2026-09-10T20:00:00"
    assert (s["usuario_id"], s["unidade_id"]) == (1, "AP-1")


def test_cartao_desconhecido_fica_sem_unidade():
    s = ingestao.normalizar({**REGISTRO_SEMS, "cardNo": "CLONADO"}, {"CARD-ANA": (1, "AP-1")})
    assert s["unidade_id"] is None


class ClienteFalso:
    def buscar_sessoes(self, dia):
        return [REGISTRO_SEMS]


def test_ingestao_nao_duplica_sessoes():
    conn = database.conectar(":memory:")
    database.criar_tabelas(conn)
    database.cadastrar_carregador(conn, "CHG-1", "HCA G2", 7.0, "teste")

    primeira = ingestao.ingerir_periodo(conn, ClienteFalso(), date(2026, 9, 10), date(2026, 9, 10))
    segunda = ingestao.ingerir_periodo(conn, ClienteFalso(), date(2026, 9, 10), date(2026, 9, 10))
    assert primeira["novas"] == 1
    assert segunda["duplicadas"] == 1
    assert conn.execute("SELECT COUNT(*) FROM sessoes").fetchone()[0] == 1


def sessao(**valores):
    base = {"id": 1, "unidade_id": "AP-1", "cartao_rfid": "CARD-ANA", "kwh": 18.0,
            "duracao_min": 180.0, "potencia_media_kw": 6.0, "potencia_nominal_kw": 7.0}
    return pd.Series({**base, **valores})


def test_regra_sessao_longa_sem_energia():
    motivos = anomalias.regras_fisicas(sessao(kwh=0.0, duracao_min=240, potencia_media_kw=0.0))
    assert any("sem energia" in m for m in motivos)


def test_regra_potencia_acima_do_carregador():
    motivos = anomalias.regras_fisicas(sessao(kwh=40.0, duracao_min=120, potencia_media_kw=20.0))
    assert any("acima do máximo" in m for m in motivos)


def test_regra_cartao_nao_cadastrado():
    assert anomalias.regras_fisicas(sessao(unidade_id=None))


def test_sessao_normal_passa_nas_regras():
    assert anomalias.regras_fisicas(sessao()) == []


def test_zscore_compara_com_historico_da_propria_unidade():
    historico = pd.DataFrame({"id": range(2, 12), "kwh": [18, 20, 19, 21, 17, 22, 18, 20, 19, 21]})
    assert anomalias.zscore_consumo(sessao(kwh=60.0), historico) > 3
    assert abs(anomalias.zscore_consumo(sessao(kwh=19.5), historico)) < 1


def test_zscore_ignora_historico_curto():
    historico = pd.DataFrame({"id": [2, 3], "kwh": [18, 20]})
    assert anomalias.zscore_consumo(sessao(kwh=60.0), historico) is None


def test_gestor_nao_aprova_sessao_sem_unidade():
    conn = database.conectar(":memory:")
    database.criar_tabelas(conn)
    database.cadastrar_carregador(conn, "CHG-1", "HCA G2", 7.0, "teste")
    conn.execute(
        """INSERT INTO sessoes (id_externo, carregador_id, cartao_rfid, inicio, fim, duracao_min, kwh,
           potencia_media_kw, potencia_pico_kw, status, ingerido_em)
           VALUES ('X', 'CHG-1', 'CLONADO', '2026-09-01', '2026-09-01', 60, 5, 5, 5, 'em_revisao', 'agora')"""
    )
    with pytest.raises(ValueError, match="Cartão não cadastrado"):
        anomalias.revisar_sessao(conn, 1, aprovar=True)
