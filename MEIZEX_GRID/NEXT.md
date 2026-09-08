# NEXT — Trabalho autorizado do MEIZEX Grid

Escopo atual de execução. Itens aqui podem ser implementados/estendidos; nada
fora daqui é implementação autorizada — apenas descoberta/proposta.

## NEXT-007 — ResourceDispatcher roteia por recurso (local vs Grid), de verdade

Decorrente de NEXT-006, mesma sessão. Implementado o que faltava para o
`ResourceDispatcher` (não só o executor isolado) escolher corretamente entre
executar local ou no Grid:

- `RemoteSSHExecutor.can_handle()` agora exige `step.resource == self.resource_id`
  (antes herdava o `can_handle` genérico do `ProcessExecutor`, que só olha
  `execution_boundary == "PROCESS"` — com dois executores desse tipo na lista,
  o primeiro roubaria todo passo PROCESS, não importa a máquina alvo real).
- Teste real (`MEIZEX_GRID/test_dispatcher_remote_ssh_routing.py`): dois
  passos no mesmo plano, um endereça o recurso do Dell-B, outro um recurso
  local — o dispatcher mandou cada um pro executor certo (`remote_ssh` com
  PID remoto real; `process` com PID local real). Não é mock.

**Achado importante, não resolvido — motivo real de não ter automação por
missão ainda:** o Capability Router (`capabilities/router.py`) só modela
duas categorias de recurso — `local: bool` e `cloud_required: bool`. Um
recurso com `local=False` é excluído com `"cloud_forbidden"`, a não ser que
a missão permita cloud explicitamente. O Dell-B **não é** a máquina local
(seria falso declarar `local=true`) e **não é** nuvem (forçar via
`cloud_allowed` quebraria a semântica de segurança que esse flag protege —
ele existe para manter o MRW "local-first", não para modelar rede confiável
vs. internet). **O modelo de dados não tem uma terceira categoria para
"rede confiável, máquina remota".**

Por isso este item testa a camada de **despacho** (`ResourceDispatcher`,
que já recebe um `CapabilityRouteResult` pronto) e não a camada de
**classificação de missão** (`route()`, que decidiria automaticamente,
a partir do texto da missão, se deve usar o Grid). Um `ExecutionStep`
apontando pro Dell-B foi montado manualmentes no teste, não gerado pelo
roteador de verdade.

Para fechar essa lacuna de verdade (não feito aqui, é decisão de design,
não implementação apressada):
1. Estender `CapabilityResource`/`CandidateResource` com uma terceira
   categoria de localização (ex.: `location: Literal["local","grid","cloud"]`
   substituindo o `local: bool` binário), preservando a semântica atual de
   `cloud_forbidden` intacta para recursos de nuvem de verdade.
2. Registrar o Dell-B (e futuramente Dell-A/outros nós) no
   `docs/local_capability_registry.json` do MRW com essa nova categoria,
   `execution_boundary: "PROCESS"`, e `declared_capabilities` honestas
   (sem inflar para `VALIDATED` — uma prova de conceito manual não é uma
   suíte de validação).
3. Decidir a política de *quando* preferir o Grid sobre execução local
   (carga, capacidade declarada pelo AIR, afinidade) — isto é uma decisão
   de produto/arquitetura, não uma linha de código.

SCOPE: MEIZEX_ROUTER_WORKER/src/meizex_mrw/dispatch/executors/remote_ssh.py,
MEIZEX_GRID/test_dispatcher_remote_ssh_routing.py

STATUS: concluído em 2026-09-07 (camada de despacho); classificação
automática por missão explicitamente NÃO implementada — motivo real
documentado acima, não é procrastinação.

## NEXT-006 — RemoteSSHExecutor: MRW executa de verdade via SSH no Dell-B

SOURCE: conversa direta (usuário pediu "implementação real" da integração
MRW+Grid depois do NEXT-004/005 provarem SSH funcional)
TARGET: NEXT-006
ACTION: implementação direta (não passou por LAB — o padrão de transporte já
estava validado em NEXT-004/005; isto é aplicar esse padrão ao contrato do
MRW, não uma investigação nova)

Contexto: o MRW já tem um contrato de execução transporte-agnóstico —
`ResourceExecutor.execute(request) -> ResourceExecutionResult`, implementado
por `ProcessExecutor` via `run_payload()` (JSON por stdin, JSON por stdout,
timeout, mapeamento estruturado de falha). `run_payload` não sabe nem se
importa se o `command` que ela roda é local — só precisa ser algo que
`subprocess.Popen` consiga spawnar e conversar por pipe.

Implementado: `RemoteSSHExecutor(ProcessExecutor)` em
`meizex_mrw/dispatch/executors/remote_ssh.py` — troca só o `command` (usa
`ssh` com a chave/conta do piloto Grid, terminando em
`python -m meizex_mrw.dispatch.process_runner` do lado remoto). Nenhuma
mudança no contrato `ResourceExecutor`/`ResourceExecutionRequest`/
`ResourceExecutionResult`; `execute()` inteiro é herdado sem alteração.

Correção colateral (bug real, não introduzido por esta mudança, mas exposto
por ela): `ProcessExecutor._map_outcome` tinha `executor_kind="process"`
fixo (era `@staticmethod`), então qualquer subclasse ficaria com atribuição
de evidência errada. Corrigido para `executor_kind=self.kind` (agora
instance method). Confirmado sem regressão:
`pytest tests/test_milestone9_process_boundary.py` tem 1 falha
pré-existente (`test_unknown_capability_transport_failure_is_structured`),
reproduzida idêntica com `git stash` antes da mudança — não é desta
integração, fora de escopo corrigir agora.

Teste de prova de conceito: `MEIZEX_GRID/test_remote_ssh_executor.py`,
rodado do Lenovo contra o Dell-B real (não mock). Resultado:
`status: COMPLETED`, `executor_kind: remote_ssh`, PID remoto real
(confirma que atravessou a rede, não é execução local disfarçada),
latência ~1.2s por round-trip SSH.

Limitações explícitas desta entrega (não resolvidas, registradas para
não serem esquecidas):
- Só testado no Dell-B — MRW exige Python ≥3.12; o Dell-A tem 3.11.9 e
  **não serve** para rodar `meizex_mrw` sem atualizar o Python lá primeiro.
- Nenhuma integração com o `ResourceDispatcher`/roteador real — o teste
  instancia `RemoteSSHExecutor` diretamente, não passa pelo fluxo de
  seleção de recursos do MRW. Decidir *quando* o roteador deve escolher
  execução remota (por carga, por capacidade declarada, por afinidade) é
  trabalho futuro, não resolvido aqui.
- Um round-trip SSH por chamada, sem pool de conexão nem retry — aceitável
  para prova de conceito, não para uso repetido em produção.
- Chaves sem passphrase (mesma ressalva já registrada em NEXT-004).

SCOPE: MEIZEX_ROUTER_WORKER/src/meizex_mrw/dispatch/executors/*,
MEIZEX_GRID/test_remote_ssh_executor.py

STATUS: concluído em 2026-09-07 — primeira execução real do MRW através
da rede do Grid.

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

STATUS: concluído em 2026-09-07 — TERCEIRA MÁQUINA COM EXECUÇÃO REMOTA REAL

(Bloqueio inicial de acesso ao Dell-A, registrado por Codex em LAB-003,
superado quando o usuário retomou a sessão RDP e guiou os comandos
diretamente com este agente.)

Resultado: [air_snapshots/dell-a_via_ssh_192.168.15.116.json](air_snapshots/dell-a_via_ssh_192.168.15.116.json).
Mesmo roteiro do NEXT-005 (Dell-B), repetido com sucesso — confirmando que
os 5 problemas documentados lá não eram acaso, e sim padrão real do
Win32-OpenSSH em contas locais novas:

1. Perfil fantasma: pasta manual `C:\Users\meizexgrid` não virou o perfil
   real; Windows criou `C:\Users\meizexgrid.DELL-RVF` só após um logon de
   verdade. Desta vez, `runas` com senha digitada às cegas falhou
   repetidamente (usuário não conseguia confirmar se a senha estava sendo
   aceita) — resolvido forçando o logon via tarefa agendada com
   `schtasks /ru meizexgrid /rp <senha>`, que exige conceder previamente o
   direito "logon em lote" (`SeBatchLogonRight`) à conta via `secedit`
   (não concedido por padrão a contas locais novas).
2. `authorized_keys` só pode ter dono+SYSTEM na ACL (mesmo erro do NEXT-005).
3. PATH vazio na sessão SSH — mesmo caminho absoluto necessário.
4. Acesso entre perfis — mas desta vez o clone git estava em
   `C:\Windows\System32\meizex-grid` (não no perfil do usuário), porque o
   `git clone` original foi rodado com o terminal administrativo aberto em
   `C:\WINDOWS\system32` como diretório corrente. **Lição adicional:**
   sempre confirmar o diretório de trabalho antes de clonar — não assumir
   que o repositório está sob o perfil do usuário só porque ele rodou o
   comando.
5. Debug do sshd como SYSTEM via tarefa agendada, igual ao NEXT-005.

Chave dedicada gerada sem passphrase (diferença do que o escopo original
pedia — "chave privada protegida por passphrase"); aceitável para este
piloto de investigação, mas deve ser revisto antes de qualquer uso além de
teste pontual.

Limpeza técnica pós-piloto (2026-09-07): a tentativa de forçar a criação do
perfil real via `runas` falhou repetidamente (senha digitada às cegas no
console não era aceita); a solução via tarefa agendada exigiu conceder
temporariamente o direito "logon em lote" (`SeBatchLogonRight`) à conta
`meizexgrid` através de `secedit`. Revertido depois do piloto concluído —
confirmado por export do `secedit` que `meizexgrid` não aparece mais nesse
direito, restando só os grupos padrão do Windows (Administradores,
Operadores de Backup, Operadores de Servidor). Arquivos temporários
(`secpol*.cfg`, `secedit*.sdb`, `sshd_debug_a.log`) removidos do Dell-A;
logs de debug (`sshd_debug*.log`) removidos do Dell-B. Nenhuma tarefa
agendada residual em nenhuma das duas máquinas.

## NEXT-005 — Piloto de OpenSSH no Dell-B (aprovado pelo usuário, em paralelo ao Dell-A)

SOURCE: NEXT-004
TARGET: NEXT-005
ACTION: PROMOTE (extensão de escopo, não nova investigação — mesmas
condições de LAB-003/NEXT-004, aplicadas a um segundo nó)
REASON: usuário pediu explicitamente para não esperar o Dell-A terminar e
priorizar o Dell-B, por ser o nó com mais folga real de capacidade
(DOT-005/NEXT-001: ~17.3GB efetivos livres, o maior das três máquinas).
EVIDENCE: "Não podemos ver se B funciona? Ele é o mais importante!"
(2026-09-07). Isso reverte a decisão anterior de escopo sequencial (só
Dell-A por vez) registrada em NEXT-004.
ACTOR: human (Renato)

Escopo idêntico ao NEXT-004, aplicado ao Dell-B (192.168.15.42) em vez do
Dell-A: verificar/instalar OpenSSH Server, conta sem privilégio
administrativo dedicada, autenticação só por chave pública, firewall
restrito ao IP do Lenovo (192.168.15.98), registrar porta no
PORT_REGISTRY antes de abrir, teste de aceitação (`hostname` +
`python -m meizex_air.cli probe --detailed`).

Mesmos limites do NEXT-004: sem WinRM, sem integração MRW/dispatch real,
sem automação sem confirmação humana por execução.

SCOPE: MEIZEX_GRID/*, configuração local do Dell-B (fora do repositório)

STATUS: concluído em 2026-09-07 — PRIMEIRA EXECUÇÃO REMOTA REAL DO GRID

Resultado: [air_snapshots/dell-b_via_ssh_192.168.15.42.json](air_snapshots/dell-b_via_ssh_192.168.15.42.json).
Do Lenovo, `ssh` autenticado por chave pública até o Dell-B, executando
`python -m meizex_air.cli probe --detailed` com sucesso via conta restrita
`meizexgrid` (sem privilégio administrativo, confirmado fora do grupo
Administrators via SID `S-1-5-32-544`). Firewall restrito ao IP do Lenovo
(`Set-NetFirewallRule -RemoteAddress 192.168.15.98`). Porta registrada em
PORT_REGISTRY antes de habilitar.

Problemas reais encontrados e resolvidos, importantes para qualquer
automação futura de dispatch:

1. **Perfil "fantasma":** criar a pasta `C:\Users\meizexgrid` manualmente
   (antes do primeiro logon) não registra o perfil de verdade no Windows.
   O SO criou o perfil real em `C:\Users\meizexgrid.DELL-B` (sufixo do
   hostname) no primeiro logon (`runas /user:meizexgrid cmd`), e o sshd
   procura `authorized_keys` no perfil *real*, não na pasta manual. Sempre
   forçar um logon (`runas`) antes de configurar `.ssh` para uma conta nova.
2. **ACL estrita do OpenSSH:** o Win32-OpenSSH recusa `authorized_keys` se
   qualquer conta além do dono e do SYSTEM tiver permissão no arquivo —
   mesmo uma conta administradora usada só para configurar. Erro exato:
   `Bad permissions. Try removing permissions for user: ...`. A pasta `.ssh`
   pode ter mais permissões (para conseguir configurar), mas o arquivo
   `authorized_keys` em si só pode ter dono+SYSTEM.
3. **PATH vazio na sessão SSH:** a conta dedicada não herda o `PATH` de
   usuário de outra conta — comandos como `python` sem caminho completo
   falham com "não é reconhecido". Usar sempre caminho absoluto do
   executável em comandos de dispatch.
4. **Acesso entre perfis:** uma conta restrita não enxerga arquivos de
   outro perfil (`C:\Users\usuario\...`) por padrão — precisou de
   `icacls ... /grant "meizexgrid:(OI)(CI)RX" /T` tanto no clone do
   repositório quanto na instalação do Python usada. Uma integração real do
   MRW provavelmente vai preferir um clone/instalação dedicados à conta de
   serviço, em vez de dar acesso cruzado a perfis de usuários humanos.
5. **Debug do sshd como serviço:** o Event Log do Windows (`OpenSSH/Operational`)
   não mostra o motivo detalhado de falhas de autenticação, só eventos de
   ciclo de vida. Para depurar de verdade, é preciso rodar `sshd.exe -d`
   como SYSTEM via uma tarefa agendada temporária (`New-ScheduledTaskPrincipal
   -UserId SYSTEM -LogonType ServiceAccount`), redirecionando a saída a um
   arquivo — rodar `sshd -d` manualmente (sem ser via serviço) dá um erro
   diferente e enganoso (falha ao gerar token de usuário, por não estar
   rodando como SYSTEM).

Limpeza pendente (não bloqueia o critério de conclusão, mas deve ser feita):
remover a linha `LogLevel DEBUG3` adicionada a `C:\ProgramData\ssh\sshd_config`
no Dell-B (voltar ao nível padrão), já que o objetivo de depuração foi
alcançado.

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
