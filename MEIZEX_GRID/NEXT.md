# NEXT — Trabalho autorizado do MEIZEX Grid

Escopo atual de execução. Itens aqui podem ser implementados/estendidos; nada
fora daqui é implementação autorizada — apenas descoberta/proposta.

## NEXT-001 — Capacidade real das três máquinas via AIR

Rodar o coletor do MEIZEX_AIR (snapshot de máquina: RAM efetiva, pressão de
processos, executores encontrados) em cada uma das três máquinas confirmadas
em DOT-005 (Lenovo 192.168.15.98, Dell-B 192.168.15.42, Dell-A
192.168.15.116), e consolidar os três relatórios num único documento/JSON
associado à identidade de rede já coletada.

Antes de confiar no resultado: `discover_lm_studio()` em
`MEIZEX_AIR/src/meizex_air/executor_probe.py:121` confirma LM Studio só por
HTTP 200 em `:1234/v1/models`, sem checar o processo — não tratar essa
detecção específica como prova de capacidade de inferência até corrigir ou
contornar. Ollama/LM Studio foram substituídos (DOT-003); descoberta de
executores deve refletir llama.cpp/runtimes atuais, não os antigos.

Critério de conclusão: as três máquinas têm um snapshot de capacidade
(RAM efetiva, não apenas RAM instalada) coletado localmente em cada uma,
identificado por IP/hostname consistente com DOT-005, com timestamp e origem
explícitos. Nenhuma execução distribuída (worker respondendo a comando
remoto) é exigida neste item.

SCOPE: MEIZEX_GRID/*, MEIZEX_AIR/src/meizex_air/*

STATUS: concluído em 2026-09-06

Resultado: [air_snapshots/lenovo_192.168.15.98.json](air_snapshots/lenovo_192.168.15.98.json)
(via CLI oficial `meizex-air probe --detailed`),
[air_snapshots/dell-a_192.168.15.116.json](air_snapshots/dell-a_192.168.15.116.json) e
[air_snapshots/dell-b_192.168.15.42.json](air_snapshots/dell-b_192.168.15.42.json)
(via [air_snapshots/standalone_probe.py](air_snapshots/standalone_probe.py) — subconjunto
independente do pacote, criado porque MEIZEX_ECOSYSTEM não estava presente nas duas
Dell; reproduz fielmente CPU/RAM/swap/disco/processos de machine_probe.py, sem os
registries de executor/artifact/formula).

Achado: Dell-B tem hoje, de longe, a maior folga real (~17.3GB efetivos livres
contra ~6.4GB do Lenovo e ~2.0GB do Dell-A) — apesar de RAM nominal ser
"só" 32GB vs 24GB do Lenovo, o uso atual de cada máquina inverte a
expectativa ingênua por RAM instalada. Dell-A está sob pressão de memória
MODERATE com pouquíssima folga segura.

Achado colateral (fora do escopo original, registrar para NEXT futuro):
nenhuma das duas Dell tem o repositório MEIZEX_ECOSYSTEM — não há
sincronização de código entre as três máquinas do Grid hoje. Qualquer
execução real de worker vai exigir decidir como o código chega em cada nó
(git clone, cópia manual, ou outro mecanismo) antes de cogitar dispatch.

## NEXT-002 — Como o código chega em cada nó, e só depois transporte de comando

Achado do NEXT-001: nenhuma das duas Dell tem o repositório MEIZEX_ECOSYSTEM.
Antes de avaliar como *disparar* um comando remoto (RDP, WinRM, MCP remoto),
o Grid precisa de uma resposta para uma pergunta mais básica — como o
*código* (MEIZEX_AIR, futuramente MRW e outros) chega em cada máquina e como
ele é mantido atualizado. Sem isso, "transporte remoto" fica sem o que
transportar além de comandos avulsos como o `standalone_probe.py` usado no
NEXT-001 (que é um contorno pontual, não solução — ele reimplementa um
subconjunto do AIR à mão e vai divergir do pacote real com o tempo).

Escopo desta investigação (sem implementar nada ainda):

1. Levantar opções realistas para distribuir/atualizar código nas três
   máquinas: git clone + pull manual, git clone + hook/agendador, cópia via
   compartilhamento de rede, sincronização tipo Syncthing/OneDrive já em uso
   na rede, ou outro mecanismo. Considerar que WinRM está desligado por
   padrão nas máquinas testadas (Dell-B confirmado) e que abrir portas novas
   na rede exige justificar cada uma via PORT_REGISTRY.
2. Só depois de ter uma resposta para (1), retomar a pergunta original:
   se RDP (já comprovado acessível do Lenovo para o Dell-B, ver DOT-005) ou
   outra via serve como transporte de *comando* remoto para o Grid, ou se
   deve ficar reservado a uso manual e o Grid usar outro mecanismo (ex.: MCP
   remoto, ainda não resolvido em MEIZEXLF-MCP).

Não iniciar implementação de dispatch remoto nem de sincronização de código
a partir deste item sem promoção explícita para um NEXT dedicado — este item
é investigação e proposta, não execução.

SCOPE: nenhum (investigação apenas, sem alteração de código)

STATUS: parcialmente concluído em 2026-09-06 — item 1 resolvido (ver DOT-007:
git remoto privado https://github.com/Renato-RVF/meizex-grid, promovido de
LAB-001). Item 2 (RDP/WinRM como transporte de *comando*) continua aberto,
sem decisão nem investigação adicional ainda.

## NEXT-003 — Validar `git clone` nas duas Dell e rodar o AIR oficial

Decorrente de DOT-007. Hoje as três máquinas têm capacidade medida via
`air_snapshots/standalone_probe.py` (contorno manual do NEXT-001). Com o
repositório https://github.com/Renato-RVF/meizex-grid disponível, clonar
nas duas Dell e rodar o pacote `meizex_air` oficial completo
(`pip install -e .` + `meizex-air probe --detailed` ou
`python -m meizex_air.cli probe --detailed`), substituindo o standalone por
uma coleta com os registries de executor/artifact/formula que o standalone
não cobre.

Critério de conclusão: `git clone` bem-sucedido em Dell-A e Dell-B, e um
snapshot via CLI oficial (não o standalone) salvo para cada uma, comparável
ao já existente do Lenovo.

SCOPE: MEIZEX_GRID/air_snapshots/*

STATUS: aberto
