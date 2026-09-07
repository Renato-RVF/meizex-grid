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

STATUS: parcialmente concluído — item 1 resolvido em 2026-09-06 (ver DOT-007:
git remoto privado https://github.com/Renato-RVF/meizex-grid, promovido de
LAB-001). Item 2 (transporte de *comando*) investigado em 2026-09-07 pelo
Codex (GPT-6 Astra) — ver LAB-003: OpenSSH é o candidato preferido, mas a
porta 22 está fechada nas três máquinas hoje (conexão recusada, sem serviço
ativo); nenhum piloto autenticado foi executado. Segue sem decisão de
produção — habilitar SSH no Dell-A exige aprovação explícita do usuário,
não é uma promoção automática deste achado.

## NEXT-004 — Piloto de OpenSSH no Dell-A (aprovado pelo usuário)

SOURCE: LAB-003
TARGET: NEXT-004
ACTION: PROMOTE
REASON: usuário aprovou explicitamente, em 2026-09-07, habilitar um piloto
de OpenSSH restrito ao Dell-A, seguindo exatamente as condições já propostas
pelo Codex na investigação de LAB-003 (chave pública, conta sem privilégio
administrativo, firewall limitado ao IP do Lenovo, sem WinRM).
EVIDENCE: aprovação em chat, "Vamos aprovar o piloto no Dell-A" (2026-09-07).
ACTOR: human (Renato)

Escopo autorizado — só o que está listado abaixo, nada além:

1. Verificar no Dell-A, sem alterar nada ainda, se a feature OpenSSH Server
   está disponível/instalada e se o serviço `sshd` existe (mesmo que
   parado).
2. Habilitar o serviço OpenSSH Server **somente no Dell-A** (não no Lenovo
   nem no Dell-B nesta rodada).
3. Criar ou usar uma conta **sem privilégios administrativos** no Dell-A
   dedicada a este acesso — não usar a conta pessoal do usuário nem uma
   conta admin existente.
4. Autenticação **só por chave pública** — gerar o par no Lenovo (chave
   privada protegida por passphrase, nunca sai do Lenovo), provisionar a
   chave pública em `authorized_keys` da conta dedicada no Dell-A.
5. Regra de firewall no Dell-A limitando a porta SSH ao IP do Lenovo
   (192.168.15.98), não aberta para toda a rede.
6. Registrar a alocação de porta usada no
   `PORT_REGISTRY/localhost.md` antes de abrir de fato (não só consultar
   como fez o LAB-003).
7. Teste de aceitação: do Lenovo, `ssh` autenticado por chave até o Dell-A
   executando `hostname` e depois `python -m meizex_air.cli probe --detailed`
   com sucesso (código de saída 0, saída válida).

Explicitamente fora de escopo nesta rodada: WinRM, habilitar SSH no Lenovo
ou Dell-B, integração com o MRW/dispatch real, qualquer automação que rode
comandos remotos sem confirmação humana por execução.

Critério de conclusão: teste de aceitação (item 7) bem-sucedido, com
evidência (log/saída) commitada no repositório, e a alocação de porta
registrada no PORT_REGISTRY.

SCOPE: MEIZEX_GRID/*, configuração local do Dell-A (fora do repositório —
documentar o que foi feito na máquina, não é código versionável)

STATUS: aprovado para execução, bloqueado em 2026-09-07 no acesso inicial
ao Dell-A. SSH :22 recusado; sessão atual sem controle local/RDP do destino.
Ver atualização de LAB-003. Nenhuma configuração alterada; teste de aceitação
ainda não realizado.

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

STATUS: concluído em 2026-09-07

Resultado: [air_snapshots/dell-a_official_192.168.15.116.json](air_snapshots/dell-a_official_192.168.15.116.json)
e [air_snapshots/dell-b_official_192.168.15.42.json](air_snapshots/dell-b_official_192.168.15.42.json),
ambos via `python -m meizex_air.cli probe --detailed` após `git clone` do
repositório https://github.com/Renato-RVF/meizex-grid.

Achados:
- Confirma DOT-003: Ollama e LM Studio aparecem `NOT_FOUND` nas três máquinas
  (Lenovo ainda não testado com o CLI oficial, mas Dell-A e Dell-B sim) — sem
  falso positivo do bug conhecido em `executor_probe.py:121`, já que nenhum
  serviço respondia nessas portas.
- Nenhum executor de inferência real (Ollama/LM Studio/llama.cpp) confirmado
  em nenhuma máquina testada até agora — o Grid hoje não tem onde rodar
  inferência de modelo, só execução de código Python/PowerShell.
- Dell-A tem ONNX Runtime confirmado (Python 3.11.9); Dell-B não tem
  (Python 3.13.3) — diferença de ambiente entre as duas, não avaliado se é
  relevante para o Grid ainda.
- `git clone` funcionou nas duas Dell sem maiores problemas depois de
  resolvida uma trava de 2FA da conta GitHub (login preso em
  `sessions/two-factor/app`, resolvido com um código de recuperação salvo em
  `MEIZEX_VAULT/github-recovery-codes.txt`).
