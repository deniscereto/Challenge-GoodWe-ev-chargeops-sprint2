"""
Simulador de um condomínio com carregadores GoodWe HCA G2.

Como ainda não temos credenciais da API SEMS (risco apontado na Sprint 1,
seção 3.3), este módulo gera um histórico realista de sessões de recarga e
grava cada dia num arquivo JSON no formato que imaginamos para a resposta
da API. O serviço de ingestão lê esses arquivos exatamente como leria a API.

Também injetamos alguns problemas de propósito (falha de medição, cartão não
cadastrado, consumo fora do padrão...). O "gabarito" dessas anomalias fica em
um arquivo separado, usado APENAS para medir se a IA consegue encontrá-las.
"""

import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from chargeops import config

# ---------------------------------------------------------------------------
# Cadastro do condomínio fictício
# ---------------------------------------------------------------------------
CARREGADORES = [
    # serial,              modelo,        potência kW, local
    ("HCA7K-G2-0001", "GoodWe HCA G2 7kW", 7.0, "Garagem G1 - vaga 12"),
    ("HCA7K-G2-0002", "GoodWe HCA G2 7kW", 7.0, "Garagem G1 - vaga 13"),
]

UNIDADES = [
    # id,      bloco, responsável
    ("AP-101", "A", "Marina Lopes"),
    ("AP-102", "A", "Rafael Souza"),
    ("AP-201", "A", "Beatriz Nakamura"),
    ("AP-202", "A", "Carlos Menezes"),
    ("AP-301", "A", "Juliana Prado"),
    ("AP-302", "A", "Eduardo Lima"),
    ("AP-401", "B", "Fernanda Rocha"),
    ("AP-402", "B", "Gustavo Alves"),
    ("AP-501", "B", "Helena Castro"),
    ("AP-502", "B", "Igor Tavares"),
    ("AP-601", "B", "Larissa Duarte"),
    ("AP-602", "B", "Mateus Ribeiro"),   # sem veículo elétrico: nunca deve pagar nada
]


@dataclass
class PerfilUsuario:
    nome: str
    cartao: str
    unidade: str
    veiculo: str
    potencia_max_veiculo_kw: float   # limite do carregador de bordo do carro
    perfil: str                      # 'noturno', 'diurno' ou 'ocasional'
    kwh_tipico: float                # energia média por recarga
    ativo_de: date
    ativo_ate: date


INICIO_PADRAO = date(2026, 7, 1)
FIM_PADRAO = date(2026, 9, 30)

USUARIOS = [
    # AP-101 tem dois carros: as sessões dos dois somam numa fatura só.
    PerfilUsuario("Marina Lopes", "RFID-A1B2C3", "AP-101", "BYD Dolphin", 6.6, "noturno", 22, INICIO_PADRAO, FIM_PADRAO),
    PerfilUsuario("Pedro Lopes", "RFID-A1B2C4", "AP-101", "Renault Kwid E-Tech", 6.6, "ocasional", 14, INICIO_PADRAO, FIM_PADRAO),
    PerfilUsuario("Rafael Souza", "RFID-D4E5F6", "AP-102", "BYD Seal", 7.0, "noturno", 30, INICIO_PADRAO, FIM_PADRAO),
    PerfilUsuario("Beatriz Nakamura", "RFID-112233", "AP-201", "GWM Ora 03", 6.6, "diurno", 16, INICIO_PADRAO, FIM_PADRAO),
    PerfilUsuario("Carlos Menezes", "RFID-445566", "AP-202", "Volvo EX30", 7.0, "noturno", 26, INICIO_PADRAO, FIM_PADRAO),
    PerfilUsuario("Juliana Prado", "RFID-778899", "AP-301", "BYD Dolphin Mini", 6.6, "diurno", 12, INICIO_PADRAO, FIM_PADRAO),
    # AP-302 vendeu o carro no fim de agosto: em setembro não pode pagar nada.
    PerfilUsuario("Eduardo Lima", "RFID-AABBCC", "AP-302", "Peugeot e-2008", 7.0, "ocasional", 20, INICIO_PADRAO, date(2026, 8, 28)),
    PerfilUsuario("Fernanda Rocha", "RFID-DDEEFF", "AP-401", "BYD Yuan Plus", 7.0, "noturno", 28, INICIO_PADRAO, FIM_PADRAO),
    PerfilUsuario("Gustavo Alves", "RFID-123ABC", "AP-402", "Chevrolet Bolt", 7.0, "diurno", 18, INICIO_PADRAO, FIM_PADRAO),
    PerfilUsuario("Helena Castro", "RFID-456DEF", "AP-501", "Mini Cooper SE", 7.0, "ocasional", 15, INICIO_PADRAO, FIM_PADRAO),
    # Novos moradores com carro elétrico: a demanda do condomínio cresce.
    PerfilUsuario("Igor Tavares", "RFID-789GHI", "AP-502", "BYD Dolphin", 6.6, "noturno", 21, date(2026, 8, 10), FIM_PADRAO),
    PerfilUsuario("Larissa Duarte", "RFID-0JKL12", "AP-601", "GWM Ora 03", 6.6, "noturno", 19, date(2026, 9, 1), FIM_PADRAO),
]

# Probabilidade de recarregar em um dia (seg-sex, sáb-dom) e janela de chegada.
PERFIS = {
    "noturno":   {"prob_semana": 0.32, "prob_fds": 0.20, "horas": (18.5, 23.0)},
    "diurno":    {"prob_semana": 0.28, "prob_fds": 0.12, "horas": (8.0, 15.0)},
    "ocasional": {"prob_semana": 0.12, "prob_fds": 0.22, "horas": (9.0, 21.0)},
}

PROB_ANOMALIA = 0.05  # ~5% das sessões recebem um problema injetado


def cadastrar_condominio(conn) -> None:
    """Grava no banco os carregadores, unidades e moradores do condomínio fictício."""
    from chargeops import database

    for serial, modelo, potencia, local in CARREGADORES:
        database.cadastrar_carregador(conn, serial, modelo, potencia, local)
    for id_unidade, bloco, responsavel in UNIDADES:
        database.cadastrar_unidade(conn, id_unidade, bloco, responsavel)
    for u in USUARIOS:
        database.cadastrar_usuario(conn, u.nome, u.cartao, u.unidade, u.veiculo)
    conn.commit()


# ---------------------------------------------------------------------------
# Geração das sessões
# ---------------------------------------------------------------------------
def _escolher_carregador(livre_a_partir: dict, inicio: datetime) -> tuple[str, datetime]:
    """Usa o primeiro carregador livre; se os dois estiverem ocupados, o usuário espera."""
    serial = min(livre_a_partir, key=livre_a_partir.get)
    return serial, max(inicio, livre_a_partir[serial])


def _sessao_normal(rng: random.Random, u: PerfilUsuario, potencia_carregador: float) -> dict:
    kwh = max(2.0, rng.gauss(u.kwh_tipico, u.kwh_tipico * 0.25))
    potencia = min(u.potencia_max_veiculo_kw, potencia_carregador) * rng.uniform(0.82, 0.97)
    motivo = "EV_FULL"
    # De vez em quando o morador tira o carro antes de terminar (sessão curta, mas legítima).
    if rng.random() < 0.08:
        kwh *= rng.uniform(0.15, 0.5)
        motivo = "USER_STOP"
    duracao_h = kwh / potencia
    return {"kwh": kwh, "duracao_h": duracao_h, "pico_kw": min(potencia * rng.uniform(1.0, 1.06), potencia_carregador), "motivo": motivo}


def _injetar_anomalia(rng: random.Random, sessao: dict, u: PerfilUsuario, potencia_carregador: float) -> tuple[dict, str]:
    tipo = rng.choices(
        ["sem_energia", "medicao_impossivel", "consumo_atipico", "potencia_baixa", "cartao_desconhecido"],
        weights=[2, 2, 3, 3, 1],
    )[0]
    if tipo == "potencia_baixa":
        # Sessão longuíssima com potência muito baixa: cabo com defeito, carro
        # com problema ou algo que não é um carro plugado. Energia "normal",
        # por isso só aparece quando se olha duração + potência juntas.
        potencia = rng.uniform(0.6, 1.3)
        duracao = rng.uniform(9.0, 14.0)
        sessao.update(kwh=potencia * duracao, duracao_h=duracao, pico_kw=potencia * 1.1, motivo="EV_FULL")
    elif tipo == "sem_energia":
        # Carro ficou plugado horas, mas o medidor não registrou energia (falha/erro).
        sessao.update(kwh=0.0, duracao_h=rng.uniform(1.0, 6.0), pico_kw=0.0, motivo="FAULT")
    elif tipo == "medicao_impossivel":
        # Energia registrada maior do que o carregador conseguiria entregar no tempo da sessão.
        duracao = rng.uniform(1.0, 2.5)
        sessao.update(kwh=duracao * potencia_carregador * rng.uniform(2.0, 3.5), duracao_h=duracao)
    elif tipo == "consumo_atipico":
        # Fisicamente possível, mas ~3x o consumo normal da unidade
        # (ex.: cartão emprestado para outro carro, recarga de terceiros).
        # Limitado a 75 kWh, que ainda cabe na bateria de um carro grande.
        kwh = min(u.kwh_tipico * rng.uniform(2.8, 3.5), 75.0)
        potencia = min(u.potencia_max_veiculo_kw, potencia_carregador) * 0.95
        sessao.update(kwh=kwh, duracao_h=kwh / potencia, motivo="EV_FULL")
    return sessao, tipo


def gerar_historico(
    inicio: date = INICIO_PADRAO,
    fim: date = FIM_PADRAO,
    semente: int = 3,
    pasta=None,
    caminho_gabarito=None,
) -> dict:
    """
    Gera as sessões do período e grava um JSON por dia em data/sems_mock/.
    A semente fixa faz o histórico sair igual em toda execução (reprodutível).
    """
    rng = random.Random(semente)
    pasta = pasta or config.PASTA_MOCK_SEMS
    caminho_gabarito = caminho_gabarito or config.CAMINHO_GABARITO
    pasta.mkdir(parents=True, exist_ok=True)
    for arquivo in pasta.glob("*.json"):
        arquivo.unlink()

    potencia_por_serial = {s: p for s, _, p, _ in CARREGADORES}
    gabarito = {}
    contador = 0
    total_sessoes = 0

    dia = inicio
    while dia <= fim:
        fim_de_semana = dia.weekday() >= 5
        # Monta a lista de quem vai carregar hoje e em que horário chega.
        chegadas = []
        for u in USUARIOS:
            if not (u.ativo_de <= dia <= u.ativo_ate):
                continue
            p = PERFIS[u.perfil]
            prob = p["prob_fds"] if fim_de_semana else p["prob_semana"]
            if rng.random() < prob:
                hora = rng.uniform(*p["horas"])
                chegadas.append((datetime.combine(dia, datetime.min.time()) + timedelta(hours=hora), u))
        chegadas.sort(key=lambda x: x[0])

        livre_a_partir = {s: datetime.combine(dia, datetime.min.time()) for s in potencia_por_serial}
        registros = []
        for chegada, u in chegadas:
            serial, inicio_sessao = _escolher_carregador(livre_a_partir, chegada)
            potencia_carregador = potencia_por_serial[serial]
            sessao = _sessao_normal(rng, u, potencia_carregador)
            cartao = u.cartao

            contador += 1
            order_id = f"EVO{dia:%Y%m%d}{contador:05d}"
            if rng.random() < PROB_ANOMALIA:
                sessao, tipo = _injetar_anomalia(rng, sessao, u, potencia_carregador)
                if tipo == "cartao_desconhecido":
                    cartao = f"RFID-{rng.randint(0, 0xFFFFFF):06X}"
                gabarito[order_id] = tipo

            fim_sessao = inicio_sessao + timedelta(hours=sessao["duracao_h"])
            livre_a_partir[serial] = fim_sessao + timedelta(minutes=5)

            # Formato "estilo SEMS": duração em segundos, potência em W,
            # datas em texto local. O adaptador de ingestão normaliza tudo.
            registros.append({
                "orderId": order_id,
                "sn": serial,
                "cardNo": cartao,
                "startTime": inicio_sessao.strftime("%Y-%m-%d %H:%M:%S"),
                "endTime": fim_sessao.strftime("%Y-%m-%d %H:%M:%S"),
                "chargeDuration": int(sessao["duracao_h"] * 3600),
                "chargeEnergy": round(sessao["kwh"], 2),
                "maxPower": int(sessao["pico_kw"] * 1000),
                "stopReason": sessao["motivo"],
            })

        resposta = {
            "hasError": False,
            "code": 0,
            "msg": "success",
            "data": {"date": dia.isoformat(), "records": registros},
        }
        (pasta / f"{dia.isoformat()}.json").write_text(
            json.dumps(resposta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        total_sessoes += len(registros)
        dia += timedelta(days=1)

    caminho_gabarito.write_text(json.dumps(gabarito, indent=2), encoding="utf-8")
    return {"sessoes": total_sessoes, "anomalias_injetadas": len(gabarito)}
