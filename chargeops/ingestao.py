"""
Session Service: busca as sessões no SEMS, normaliza e grava no banco.

Normalizar = converter o formato do fabricante para o nosso esquema interno:
  - duração em segundos   -> minutos
  - potência em W         -> kW
  - data em texto local   -> ISO 8601
  - cartão RFID           -> usuário e unidade (é isso que permite o rateio)
  - potência média        -> calculada por nós (kWh / horas), não confiamos no equipamento
"""

from datetime import date, datetime, timedelta


def normalizar(registro: dict, mapa_cartoes: dict) -> dict:
    inicio = datetime.strptime(registro["startTime"], "%Y-%m-%d %H:%M:%S")
    fim = datetime.strptime(registro["endTime"], "%Y-%m-%d %H:%M:%S")
    duracao_min = registro["chargeDuration"] / 60
    kwh = float(registro["chargeEnergy"])
    horas = duracao_min / 60
    potencia_media = kwh / horas if horas > 0 else 0.0

    # Cartão desconhecido -> usuario_id e unidade_id ficam vazios.
    # A sessão é gravada mesmo assim (não perdemos o dado) e a IA vai retê-la.
    usuario_id, unidade_id = mapa_cartoes.get(registro["cardNo"], (None, None))

    return {
        "id_externo": registro["orderId"],
        "carregador_id": registro["sn"],
        "cartao_rfid": registro["cardNo"],
        "usuario_id": usuario_id,
        "unidade_id": unidade_id,
        "inicio": inicio.isoformat(),
        "fim": fim.isoformat(),
        "duracao_min": round(duracao_min, 1),
        "kwh": round(kwh, 3),
        "potencia_media_kw": round(potencia_media, 3),
        "potencia_pico_kw": round(registro["maxPower"] / 1000, 3),
        "motivo_fim": registro.get("stopReason"),
    }


def _mapa_cartoes(conn) -> dict:
    linhas = conn.execute("SELECT cartao_rfid, id, unidade_id FROM usuarios").fetchall()
    return {l["cartao_rfid"]: (l["id"], l["unidade_id"]) for l in linhas}


def ingerir_periodo(conn, cliente, inicio: date, fim: date) -> dict:
    """
    Busca dia a dia e grava as sessões novas com status 'pendente'.
    Rodar duas vezes no mesmo período não duplica nada (id_externo é UNIQUE).
    """
    mapa = _mapa_cartoes(conn)
    agora = datetime.now().isoformat(timespec="seconds")
    resumo = {"lidas": 0, "novas": 0, "duplicadas": 0, "cartao_desconhecido": 0}

    dia = inicio
    while dia <= fim:
        for registro in cliente.buscar_sessoes(dia):
            resumo["lidas"] += 1
            s = normalizar(registro, mapa)
            if s["usuario_id"] is None:
                resumo["cartao_desconhecido"] += 1
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO sessoes
                    (id_externo, carregador_id, cartao_rfid, usuario_id, unidade_id,
                     inicio, fim, duracao_min, kwh, potencia_media_kw, potencia_pico_kw,
                     motivo_fim, status, ingerido_em)
                VALUES (:id_externo, :carregador_id, :cartao_rfid, :usuario_id, :unidade_id,
                        :inicio, :fim, :duracao_min, :kwh, :potencia_media_kw, :potencia_pico_kw,
                        :motivo_fim, 'pendente', :agora)
                """,
                {**s, "agora": agora},
            )
            if cursor.rowcount == 1:
                resumo["novas"] += 1
            else:
                resumo["duplicadas"] += 1
        dia += timedelta(days=1)

    conn.commit()
    return resumo
