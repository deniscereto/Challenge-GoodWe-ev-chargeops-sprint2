"""
Clientes da API GoodWe SEMS.

O resto do sistema só conhece o método `buscar_sessoes(dia)`. Assim, trocar o
mock pela API real é trocar UMA classe, sem mexer em rateio, IA ou dashboard.
"""

import json
from datetime import date

from chargeops import config


class ClienteSemsMock:
    """Lê as respostas simuladas gravadas em data/sems_mock/AAAA-MM-DD.json."""

    def __init__(self, pasta=None):
        self.pasta = pasta or config.PASTA_MOCK_SEMS

    def buscar_sessoes(self, dia: date) -> list[dict]:
        arquivo = self.pasta / f"{dia.isoformat()}.json"
        if not arquivo.exists():
            return []
        resposta = json.loads(arquivo.read_text(encoding="utf-8"))
        if resposta.get("hasError") or resposta.get("code") != 0:
            raise RuntimeError(f"SEMS retornou erro para {dia}: {resposta.get('msg')}")
        return resposta["data"]["records"]


class ClienteSemsReal:
    """
    Esqueleto da integração real, com o fluxo mapeado na Sprint 1:
      1. POST /api/v1/Common/CrossLogin  -> token
      2. consulta de detalhe do carregador (EvChargerDetail) por stationId

    Fica desativado até a equipe receber credenciais e a especificação
    completa dos endpoints pela parceria FIAP / GoodWe.
    """

    URL_LOGIN = "https://www.semsportal.com/api/v1/Common/CrossLogin"

    def __init__(self, usuario: str, senha: str, station_id: str):
        self.usuario = usuario
        self.senha = senha
        self.station_id = station_id
        self.token = None

    def buscar_sessoes(self, dia: date) -> list[dict]:
        raise NotImplementedError(
            "Integração real pendente: aguardando credenciais e documentação da API SEMS."
        )
