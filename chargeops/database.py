"""
Camada de banco de dados (SQLite).

Modelo: unidade (apartamento) -> usuários (cada um com um cartão RFID)
-> sessões de recarga -> fatura mensal por unidade.
"""

import sqlite3
from pathlib import Path

from chargeops import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS unidades (
    id          TEXT PRIMARY KEY,          -- ex.: 'AP-101'
    bloco       TEXT NOT NULL,
    responsavel TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY,
    nome        TEXT NOT NULL,
    cartao_rfid TEXT NOT NULL UNIQUE,      -- liga a sessão do carregador ao usuário
    unidade_id  TEXT NOT NULL REFERENCES unidades(id),
    veiculo     TEXT
);

CREATE TABLE IF NOT EXISTS carregadores (
    id                  TEXT PRIMARY KEY,  -- número de série do HCA G2
    modelo              TEXT NOT NULL,
    potencia_nominal_kw REAL NOT NULL,
    local               TEXT NOT NULL
);

-- status da sessão:
--   pendente   -> acabou de chegar, ainda não passou pela IA (NÃO pode ser cobrada)
--   valida     -> IA não encontrou problema, entra no rateio
--   em_revisao -> IA marcou como suspeita, fica retida até o gestor decidir
--   aprovada   -> gestor revisou e liberou a cobrança
--   rejeitada  -> gestor revisou e cancelou a cobrança
CREATE TABLE IF NOT EXISTS sessoes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    id_externo        TEXT NOT NULL UNIQUE,   -- id da sessão no SEMS (evita duplicar)
    carregador_id     TEXT NOT NULL REFERENCES carregadores(id),
    cartao_rfid       TEXT NOT NULL,
    usuario_id        INTEGER REFERENCES usuarios(id),
    unidade_id        TEXT REFERENCES unidades(id),
    inicio            TEXT NOT NULL,          -- ISO 8601
    fim               TEXT NOT NULL,
    duracao_min       REAL NOT NULL,
    kwh               REAL NOT NULL,
    potencia_media_kw REAL NOT NULL,
    potencia_pico_kw  REAL NOT NULL,
    motivo_fim        TEXT,
    status            TEXT NOT NULL DEFAULT 'pendente',
    score_anomalia    REAL,                   -- quanto menor, mais estranha (Isolation Forest)
    alerta            TEXT,                   -- explicação legível do motivo da suspeita
    revisado_em       TEXT,
    ingerido_em       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faturas (
    unidade_id         TEXT NOT NULL REFERENCES unidades(id),
    ciclo              TEXT NOT NULL,          -- 'AAAA-MM'
    kwh_cobrado        REAL NOT NULL,
    valor_energia      REAL NOT NULL,
    taxa_manutencao    REAL NOT NULL,
    taxa_plataforma    REAL NOT NULL,
    total              REAL NOT NULL,
    sessoes_cobradas   INTEGER NOT NULL,
    sessoes_em_revisao INTEGER NOT NULL,
    gerada_em          TEXT NOT NULL,
    PRIMARY KEY (unidade_id, ciclo)
);

CREATE INDEX IF NOT EXISTS idx_sessoes_unidade ON sessoes(unidade_id);
CREATE INDEX IF NOT EXISTS idx_sessoes_status  ON sessoes(status);
"""


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    """Abre o banco (cria a pasta se preciso) e devolve linhas como dicionário."""
    caminho = Path(caminho) if caminho else config.CAMINHO_BANCO
    if str(caminho) != ":memory:":
        caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def criar_tabelas(conn: sqlite3.Connection) -> None:
    conn.executescript(ESQUEMA)
    conn.commit()


def cadastrar_unidade(conn, id_unidade: str, bloco: str, responsavel: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO unidades (id, bloco, responsavel) VALUES (?, ?, ?)",
        (id_unidade, bloco, responsavel),
    )


def cadastrar_usuario(conn, nome: str, cartao_rfid: str, unidade_id: str, veiculo: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO usuarios (nome, cartao_rfid, unidade_id, veiculo) VALUES (?, ?, ?, ?)",
        (nome, cartao_rfid, unidade_id, veiculo),
    )


def cadastrar_carregador(conn, serial: str, modelo: str, potencia_kw: float, local: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO carregadores (id, modelo, potencia_nominal_kw, local) VALUES (?, ?, ?, ?)",
        (serial, modelo, potencia_kw, local),
    )
