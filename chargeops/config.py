"""
Parâmetros de negócio do EV ChargeOps.

Tudo que o gestor do condomínio poderia ajustar fica concentrado aqui,
para não ter "números mágicos" espalhados pelo código.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------
RAIZ_PROJETO = Path(__file__).resolve().parent.parent
PASTA_DADOS = RAIZ_PROJETO / "data"
CAMINHO_BANCO = PASTA_DADOS / "chargeops.db"
PASTA_MOCK_SEMS = PASTA_DADOS / "sems_mock"          # respostas JSON simuladas da API SEMS
CAMINHO_GABARITO = PASTA_DADOS / "gabarito_anomalias.json"  # só para avaliar a IA

# ---------------------------------------------------------------------------
# Modelo de rateio (Sprint 1, seção 4.3)
# ---------------------------------------------------------------------------
# Tarifa por kWh definida pelo gestor. Valor de EXEMPLO, próximo da tarifa
# residencial com tributos em SP; deve ser atualizado com a conta real.
TARIFA_KWH = 0.98  # R$/kWh

# Custo mensal de manutenção/operação do carregador. É dividido em partes
# iguais APENAS entre as unidades que tiveram ao menos 1 sessão cobrada no mês.
CUSTO_MANUTENCAO_MENSAL = 240.00  # R$

# Taxa de operação da plataforma (Sprint 1, seção 4.6): margem pequena e
# transparente, mostrada em linha separada na fatura. 0.0 desliga a taxa.
TAXA_PLATAFORMA_PCT = 0.06  # 6% sobre o valor da energia

# ---------------------------------------------------------------------------
# Detecção de anomalias (Sprint 1, seção 4.4)
# ---------------------------------------------------------------------------
# Regra física 1: sessão com energia (quase) zero mas que durou muito tempo.
KWH_MINIMO_VALIDO = 0.1           # kWh
DURACAO_LIMIAR_SEM_ENERGIA = 30   # minutos

# Regra física 2: potência média acima do que o carregador consegue entregar
# indica erro de medição. Damos 5% de folga para arredondamentos.
FOLGA_POTENCIA = 1.05

# Regra estatística: consumo muito acima do histórico da própria unidade.
LIMIAR_ZSCORE = 3.0
MIN_SESSOES_PARA_ZSCORE = 8       # abaixo disso o histórico é curto demais

# Isolation Forest: fração esperada de sessões estranhas no histórico.
CONTAMINACAO_ISOLATION_FOREST = 0.03
SEMENTE_ALEATORIA = 42

# ---------------------------------------------------------------------------
# Previsão de consumo
# ---------------------------------------------------------------------------
DIAS_PREVISAO = 30
DIAS_VALIDACAO = 14   # últimos dias guardados para medir o erro do modelo
