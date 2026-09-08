# Relatório final — MEIZEX Grid, sessão de 2026-09-06 a 09-08

## Objetivo da sessão

Retomar a ideia de um Grid de execução distribuída usando as três máquinas
da rede doméstica (Lenovo, Dell-A, Dell-B), começando do zero verificável —
sem assumir nada que não estivesse comprovado por observação real.

## Estado no início

Só existia um README no `MEIZEX_GRID`, com decisões de partida (Tkinter,
MRW como motor) e nenhuma execução distribuída implementada.

## O que foi construído, em ordem

### 1. Identidade das três máquinas (DOT-005)

Descoberta de rede real (`discovery.py`, ping sweep + ARP, extraído do app
REDE já validado) confirmou as três máquinas na mesma varredura:

| Máquina | IP | Hostname | RAM | CPU |
|---|---|---|---|---|
| Lenovo | 192.168.15.98 | LAPTOP-CCJFHC8E | 24GB | 10c/16t |
| Dell-B | 192.168.15.42 | Dell-B | 32GB | i7-1355U, 10c/12t |
| Dell-A | 192.168.15.116 | DELL-RVF | 16GB | i7-1165G7, 4c/8t |

### 2. Capacidade real via AIR (NEXT-001)

RAM efetiva (não nominal) medida nas três: Dell-B tinha, de longe, a maior
folga real (~17.3GB livres), contra ~6.4GB do Lenovo e ~2.0GB do Dell-A —
o oposto do que a RAM nominal sozinha sugeriria.

Achado colateral que virou o próximo bloqueio: nenhuma das duas Dell tinha
o código do ecossistema — não havia sincronização entre as três máquinas.

### 3. Distribuição de código via git privado (DOT-007)

`C:\PROJETOS` é um monorepo git sem remoto, nunca publicado, com material
sensível misturado (Vault). Solução: um subconjunto deliberado e limpo,
publicado como repositório **privado** próprio —
**https://github.com/Renato-RVF/meizex-grid** — contendo só MEIZEX_GRID,
MEIZEX_AIR e (depois) MEIZEX_ROUTER_WORKER. Cada Dell faz `git clone`/`git pull`
desse repositório, não do monorepo inteiro.

### 4. Piloto de SSH real nas duas Dell (NEXT-004 e NEXT-005)

OpenSSH Server habilitado em Dell-A e Dell-B, com uma conta dedicada
(`meizexgrid`) **sem privilégio administrativo**, autenticação só por
chave pública, firewall restrito ao IP do Lenovo. Primeira execução remota
real do Grid: `python -m meizex_air.cli probe --detailed` rodando via SSH
autenticado, nas duas máquinas.

Cinco problemas reais encontrados e resolvidos (documentados em detalhe no
`NEXT.md`, para não precisar redescobrir):

1. **Perfil "fantasma"** — criar a pasta do usuário manualmente antes do
   primeiro logon não registra o perfil de verdade no Windows; o sshd
   procura em outro lugar (`C:\Users\<user>.<HOSTNAME>`, ou até
   `C:\WINDOWS\.ssh` como fallback quando o perfil não existe). É preciso
   forçar um logon real antes de configurar `.ssh`.
2. **ACL estrita do Win32-OpenSSH** — `authorized_keys` só pode ter
   dono+SYSTEM na permissão; até uma conta administradora usada só para
   configurar quebra a autenticação.
3. **PATH vazio na sessão SSH** — comandos como `python` sem caminho
   completo falham; sempre usar caminho absoluto.
4. **Acesso entre perfis** — uma conta restrita não enxerga arquivos de
   outro usuário por padrão; precisa de `icacls` explícito.
5. **Debug do sshd como serviço real** — o Event Log do Windows não
   mostra o motivo de falhas de autenticação; depurar de verdade exige
   rodar `sshd -d` como SYSTEM via tarefa agendada temporária.

O acesso inicial ao Dell-A travou numa sessão do Codex (GPT-6 Astra, a quem
uma missão foi delegada) por falta de controle de RDP — documentado em
LAB-003 como bloqueio real, não ignorado.

### 5. Integração real com o MRW (NEXT-006 e NEXT-007)

O motor de execução do MEIZEX (MRW) já tinha um contrato de execução
transporte-agnóstico (`ResourceExecutor.execute()`, JSON via stdin/stdout).
Implementado `RemoteSSHExecutor`, que reaproveita esse contrato sem
nenhuma mudança — só troca o comando local por um comando SSH.

Testado de ponta a ponta, sem mock: o `ResourceDispatcher` do MRW recebeu
um plano com dois passos — um endereçando o Dell-B, outro local — e roteou
cada um para o executor certo, com PIDs reais confirmando execução em
máquinas diferentes.

Um bug real foi corrigido no caminho (`executor_kind` fixo em `"process"`
em qualquer subclasse), sem regressão nos testes existentes do MRW.

Na primeira versão deste relatório, o roteador de missão do MRW só
modelava **local** ou **cloud** — sem categoria para "rede confiável,
máquina remota" — e a seleção automática do Grid por missão ficou
registrada como lacuna, não implementada. As próximas quatro seções
fecham exatamente essa lacuna e as que apareceram no caminho.

### 6. Despacho assíncrono (NEXT-008)

Pergunta que motivou isto: "o orquestrador já consegue mandar um teste de
estresse pro Dell-B e seguir com outros sprints?" — não, a chamada era
bloqueante. `RemoteSSHExecutor.execute_async()` roda o `execute()`
original (sem alteração) numa thread de fundo e devolve um
`RemoteJobHandle` na hora. Testado contra o Dell-B: submissão em 0.6ms,
"outro trabalho" simulado rodou 6 vezes enquanto o job real (848ms de
latência) ainda estava em voo.

### 7. Cancelamento real (NEXT-009)

`RemoteJobHandle.cancel()` mata o processo SSH local, o que normalmente
derruba o processo remoto junto (consequência observada do OpenSSH, não
garantida por esta classe). Exigiu um gancho novo (`on_spawn`, opcional,
aditivo) em `run_payload`/`ProcessExecutor.execute` pra expor o processo
sem quebrar nada existente. Testado real: job cancelado termina em 54ms
(`FAILED`), contra 932ms de um job de controle não cancelado que completa
normalmente (`COMPLETED`).

### 8. Limite de concorrência por nó (NEXT-010)

Um `threading.Semaphore` por instância de `RemoteSSHExecutor` limita
quantos jobs rodam de verdade contra aquele nó ao mesmo tempo
(`max_concurrent`, padrão 4) — submissão continua não-bloqueante acima do
limite. Cancelar um job ainda na fila funciona (nunca chega a abrir SSH),
mas não fura fila: espera o slot liberar antes de checar o cancelamento.
Testado real: 5 jobs com `max_concurrent=2` completam em ~3 "ondas" de
round-trip real (2.53s total, no máximo 2 rodando ao mesmo tempo
observado).

### 9. Roteador escolhe o Grid sozinho, configurável (NEXT-011)

Pedido explícito: "deixa configurável — automático ou à decisão do
usuário/orquestrador". Resolvido estendendo o modelo de dados de forma
aditiva (nada existente muda de comportamento):

- `CapabilityResource.location: "local" | "grid" | "cloud" | None` — novo
  campo opcional; `None` (todo recurso já cadastrado) preserva o
  comportamento antigo exatamente.
- `ExecutionProfile.grid_allowed: bool = False` — o botão pedido. `False`
  (padrão) = Grid nunca escolhido sozinho, decisão manual como antes.
  `True` = o roteador pode escolher automaticamente. Mesmo padrão que
  `cloud_allowed` já usava.
- Dell-B registrado honestamente no `local_capability_registry.json` do
  MRW, com `status: "AVAILABLE"` (evidência **OBSERVED**, não
  `VALIDATED` — uma prova de conceito manual não é uma suíte de
  validação).

Suíte completa do MRW rodada antes de prosseguir: **545 testes passaram, 5
pulados, 0 falhas**.

Teste real, sem mock, fechando o ciclo inteiro: `route("some os valores
do json e agrupe por categoria")` sem autorização nunca escolhe o Grid
(excluído com `"grid_forbidden"`); com `profile.grid_allowed=True`, o
roteador **escolhe o Dell-B sozinho**, só a partir do texto da missão — e
o resultado real (não montado à mão) foi despachado com sucesso contra o
Dell-B, PID remoto confirmado.

Achado colateral registrado, não escondido: o planejador determinístico
do MRW (`InvocationResolver`) ainda não sabe montar a invocação pra
capacidades de fronteira `PROCESS` além das duas que já conhecia — lacuna
separada, pré-existente, não resolvida nesta sessão.

## Estado final do Grid

- Três máquinas identificadas, com capacidade real medida.
- Código sincronizado via git privado entre as três.
- Execução remota real e testada nas duas Dell via SSH, com conta
  restrita e firewall travado.
- MRW com um caminho real (não teórico) de execução remota: síncrono
  (despacho normal), assíncrono (não bloqueia o chamador), cancelável,
  com limite de concorrência por nó, e com **seleção automática
  configurável** — o roteador escolhe o Grid sozinho quando autorizado
  por perfil, e nunca quando não.
- Nenhuma lacuna da lista original ficou sem resposta: as quatro
  perguntas em aberto ao fim da primeira versão deste relatório
  (despacho assíncrono, cancelamento, limite de concorrência, seleção
  automática) foram implementadas e testadas de verdade, não só
  documentadas como "próximo passo".

## Onde encontrar tudo

- Repositório: https://github.com/Renato-RVF/meizex-grid
- Decisões aceitas: `MEIZEX_GRID/DOT.md`
- Trabalho autorizado e resultado de cada item: `MEIZEX_GRID/NEXT.md`
  (NEXT-001 a NEXT-011)
- Hipóteses e investigações: `MEIZEX_GRID/LAB.md`
- Evidências de capacidade e execução remota: `MEIZEX_GRID/air_snapshots/`
- Testes reais de integração MRW:
  `MEIZEX_GRID/test_remote_ssh_executor.py`,
  `MEIZEX_GRID/test_dispatcher_remote_ssh_routing.py`,
  `MEIZEX_GRID/test_remote_ssh_async.py`,
  `MEIZEX_GRID/test_remote_ssh_cancel.py`,
  `MEIZEX_GRID/test_remote_ssh_concurrency.py`,
  `MEIZEX_GRID/test_router_grid_auto_selection.py`
