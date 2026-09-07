# MEIZEX Grid

Status: retomada em definição; nenhuma execução distribuída implementada.

## Objetivo

Disponibilizar as três máquinas da rede como recursos de execução para o
MEIZEX Router Worker (MRW), começando pela descoberta verificável da rede.

## Decisões de partida

- A interface desktop do Grid será construída com Tkinter, seguindo a tecnologia
  do app REDE. As operações de rede devem rodar fora da thread da interface,
  com resultados encaminhados à interface por fila e atualizações via after().

- MRW é o motor de roteamento e execução. Avaliar seus contratos existentes
  antes de criar coordenação, dispatch ou persistência adicionais.
- Ollama foi desinstalado e LM Studio foi substituído por llama.cpp e runtimes
  relacionados. Referências antigas não comprovam serviços ativos.
- Preservar o app independente C:/PROJETOS/REDE como referência funcional.
- Comparar REDE com MEIZEX_PIP/meizex-network-discovery antes de implementar
  outra descoberta. Presença no disco não comprova funcionamento.
- Consultar PORT_REGISTRY/check_port.py e manter localhost.md atualizado antes
  de atribuir qualquer porta. Nenhuma porta está atribuída a este projeto.
- Os projetos anteriores MEIZEX_COMPUTER_GRID e MEIZEX_GRID_CORE foram removidos;
  sua existência histórica não implica código ou integração disponível.

## Primeira entrega proposta

Identificar as três máquinas por observações atuais de rede, apresentando
identidade conhecida, endereços, origem da observação e instante da coleta.
Distinguir dispositivo observado, serviço acessível e capacidade de execução
comprovada. Não declarar uma máquina ausente apenas porque não respondeu a ping.

Critério de aceitação: as três máquinas são identificadas em uma verificação
real e os casos de ausência de resposta ou identificação incerta são explícitos.
Disponibilidade do worker será verificada separadamente.

## Investigação antes da implementação

1. Comparar os dois componentes de descoberta existentes.
2. Verificar os contratos de capabilities e dispatch do MRW.
3. Examinar AIR, GGUF Formula e PORT_REGISTRY como fontes complementares.
4. Avaliar transporte remoto, identidade do worker e recuperação de falhas
   apenas depois da descoberta básica comprovada.

## Fontes

- ../ARCHITECTURE.md: histórico de decisões; cruzar correções posteriores.
- ../MEIZEX_ROUTER_WORKER: motor atual; confirmar comportamento no código.
- C:/PROJETOS/REDE/network_mapper.py: app independente de descoberta.
- ../MEIZEX_PIP/meizex-network-discovery: candidato a reaproveitamento.
- ../PORT_REGISTRY: alocação de portas e monitoramento local.
- ../.meizex_forensics/jobs/89bbcbe4b387/forensics_report.md: inventário parcial.

O inventário de C: não cobre os componentes migrados para D:. Resultados de
testes históricos não substituem validação atual das integrações escolhidas.

