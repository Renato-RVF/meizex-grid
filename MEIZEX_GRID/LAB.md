# LAB — Conhecimento experimental do MEIZEX Grid

Sem autoridade. Hipóteses e opções levantadas; nada aqui é decisão nem
implementação autorizada. Promoção para NEXT exige decisão explícita do
steward (ver protocolo DOT/NEXT/LAB/ARC).

## LAB-001 — Como distribuir código para Dell-A e Dell-B

Contexto: NEXT-001 revelou que nenhuma das duas Dell tem o repositório
MEIZEX_ECOSYSTEM. Investigado em 2026-09-06 (NEXT-002, sem alteração de
código):

- `C:\PROJETOS` é um repositório git, branch `main`, **sem remoto
  configurado** — nunca foi publicado. Hoje o código só existe no Lenovo.
- `gh` (GitHub CLI) não está instalado/autenticado nesta máquina.
- SMB já ativo no Lenovo, mas só compartilhamentos administrativos padrão
  (`C$`, `D$`, `E$`, `ADMIN$`) e um `Users` — todos exigem credencial de
  administrador para acesso remoto; nenhum compartilhamento dedicado ao Grid.
- As três máquinas estão no mesmo WORKGROUP padrão do Windows.
- Nenhuma evidência de Syncthing/OneDrive/rsync já em uso no ecossistema.

Opções levantadas (nenhuma implementada):

1. **Repositório git remoto privado (ex.: GitHub privado).** Prós: versionado,
   histórico, `git pull` simples nas Dell, ferramenta que o usuário já usa
   (gh/PR mencionados no ecossistema). Contras: exige criar/configurar o
   remoto e decidir o que entra (o repo tem material sensível/pessoal
   misturado — ver MEIZEX_VAULT; precisaria de `.gitignore`/subset, não o
   monorepo inteiro).
2. **Compartilhamento SMB dedicado (não administrativo) no Lenovo,** com uma
   conta/senha específica para o Grid. Prós: sem depender de serviço externo.
   Contras: expõe uma pasta pela rede, exige gestão de credencial (que este
   agente não pode configurar sozinho — entrada de senha é ação proibida),
   mais superfície de ataque numa rede doméstica.
3. **Cópia manual pontual (o que já fizemos no NEXT-001)** — funciona, mas
   não escala: cada atualização de código exige repetir o processo manual,
   e diverge do original com o tempo (o `standalone_probe.py` já é uma
   evidência disso).
4. **Ferramenta de sync tipo Syncthing** (par-a-par, sem servidor central,
   only entre as três máquinas). Prós: não depende de serviço externo nem
   GitHub; sincroniza automaticamente. Contras: ferramenta nova a instalar
   e confiar nas três máquinas; sincroniza indiscriminadamente (precisaria
   apontar só para os projetos relevantes ao Grid, não o monorepo inteiro
   com material sensível).

STATUS: promovido para DOT-007 em 2026-09-06 (opção 1, git remoto privado)

SOURCE: LAB-001
TARGET: DOT-007
ACTION: PROMOTE
REASON: usuário já tinha conta GitHub privada em uso ativo (Renato-RVF),
tornando a opção de menor custo de implementação; demais opções (SMB
dedicado, Syncthing) exigiriam configurar credencial/ferramenta nova.
EVIDENCE: repositório https://github.com/Renato-RVF/meizex-grid criado
(privado) e primeiro push bem-sucedido (commit 6f12b5d, 34 arquivos) em
2026-09-06.
ACTOR: human (usuário criou o repo e executou o git push manualmente)

## LAB-002 — Comunicação direta entre agentes (Claude e Codex) no Grid

Contexto: em 2026-09-07, ao delegar uma missão de investigação (transporte
remoto de comando, ver `MISSAO_CODEX_dispatch_remoto.md`) para o Codex
(GPT-6 Astra), ficou evidente que não existe canal direto entre este agente
(Claude, operando via Claude Code) e o Codex. A troca de trabalho hoje é:
Claude escreve missão em arquivo → usuário copia/cola para o Codex → Codex
trabalha e commita no repositório → Claude só vê o resultado na próxima vez
que ler o repositório. Funciona, mas é assíncrono e depende do usuário como
intermediário manual em cada etapa.

Ferramentas de listagem de agentes deste ambiente (`ListAgents`) só
enxergam sessões do próprio Claude Code (interativas ou Remote Control) —
Codex CLI é uma ferramenta de outro fornecedor, sem integração nativa.

Hipótese, sem investigação nem implementação ainda: um canal mais direto
exigiria algo como um MCP server compartilhado que ambos os agentes possam
ler/escrever, ou um mecanismo de webhook/fila que dispare notificação
quando um dos lados commita trabalho — reduzindo o vaivém manual do
usuário a só aprovações, não a colar texto de um lado para o outro.

Não promovido: o custo de construir esse canal só se justifica se o
vaivém manual via git (já funcional, ver LAB-001/DOT-007) realmente virar
gargalo na prática — hipótese ainda não testada contra uso real repetido.

STATUS: experimental — aguardando se o padrão de trabalho manual (arquivo
de missão → colar no Codex → commit → Claude relê) se mostra insuficiente
antes de investir em integração.

## LAB-003 — Transporte de comando remoto por OpenSSH

Nota de numeração: a missão solicitou LAB-002, mas o commit concorrente
d472f06 ocupou esse identificador com comunicação entre agentes. A investigação
SSH fica em LAB-003 para preservar ambos os registros; referências LAB-002
nos comandos abaixo reproduzem os rótulos usados durante a coleta.

Contexto: investigação de NEXT-002 em 2026-09-07, autorizada pela missão
MISSAO_CODEX_dispatch_remoto.md e pelo usuário como investigação/prototipagem.
DOT-007 já resolve distribuição por git; NEXT-003 registra AIR oficial nas
Dell. Esta entrada não promove SSH a decisão nem autoriza produção.

### Evidência observada

Origem: Lenovo, hostname local LAPTOP-CCJFHC8E. Cliente disponível em
C:\Windows\System32\OpenSSH\ssh.exe, OpenSSH_for_Windows_9.5p2,
LibreSSL 3.8.2. Get-Service sshd retornou serviço inexistente. A consulta
Get-WindowsCapability -Online -Name 'OpenSSH*' exigiu elevação; estado da
feature não foi confirmado. Não foi feita instalação ou alteração de serviço.

Conexões TCP via socket.create_connection, timeout de 3 segundos, aos IPs
estabelecidos em DOT-005:

| Destino | Porta | Resultado |
|---|---|---|
| Lenovo 192.168.15.98 | 22 | WinError 10061: conexão recusada |
| Dell-A 192.168.15.116 | 22 | WinError 10061: conexão recusada |
| Dell-B 192.168.15.42 | 22 | WinError 10061: conexão recusada |

Não houve banner SSH. Isso demonstra inacessibilidade na porta padrão nesta
coleta, não ausência da feature nas Dell, nem ausência de SSH em outra porta.
Não houve sessão autenticada para confirmar hostname/instalação remotos;
os nomes da tabela são a associação histórica de DOT-005.

Teste adicional real do cliente no Lenovo:

```powershell
ssh -F none -o BatchMode=yes -o StrictHostKeyChecking=yes -o PreferredAuthentications=publickey -o ConnectTimeout=5 -o ConnectionAttempts=1 -T 192.168.15.116 hostname
```

Resultado: falha antes da autenticação, stderr `banner exchange: Connection
to UNKNOWN port -1: Connection refused`. Nenhum hostname ou snapshot retornou.
O usuário remoto nem chegou a ser validado. Não é prova de dispatch funcional.

PORT_REGISTRY/check_port.py 22 --name 'Grid SSH LAB-002 (consulta, sem reserva)'
retornou código 0: livre e não reservada no Lenovo. localhost.md consultado;
nenhuma porta foi atribuída ou aberta, portanto nenhuma reserva adicionada.
Esse resultado local não comprova disponibilidade nas Dell.

### Opções, prós e contras

1. **OpenSSH com chave pública — candidato preferido para piloto.** Cliente
   já presente no Lenovo, execução não interativa e captura de saída simples.
   Exige servidor no destino, gestão de chaves, confiança na chave do host e
   regra de firewall limitada. Não é necessário instalar servidor no Lenovo
   para ele enviar comandos. Disponível como feature opcional no Windows
   10/11 compatível; disponibilidade não implica serviço ativo.
2. **WinRM/PowerShell Remoting.** Bom suporte a objetos PowerShell; exige
   configuração de autenticação e endpoint em WORKGROUP. Dell-B tinha WinRM
   desligado segundo a missão; não revalidado aqui. Sem vantagem demonstrada
   que justifique habilitar outro transporte agora.
3. **RDP manual.** Já conhecido pelo usuário, útil para verificar/configurar
   o destino sob aprovação. Sessão interativa não oferece contrato de dispatch.
4. **Agente que busca trabalhos (pull).** Pode evitar listener nas Dell, mas
   introduz fila, autenticação e ciclo de vida próprios; escopo maior do que
   este experimento. SMB distribui arquivos, não executa comandos sozinho.

Conforme DOT-002, foram lidos no MRW os contratos ResourceExecutor e
ResourceExecutionRequest/ResourceExecutionResult em src/meizex_mrw/dispatch.
Já existem execute(request), timeout_s, run_id/step_id, status, output, error,
evidence e latency_ms. Uma integração futura deve avaliar esse contrato e o
ProcessExecutor existente; SSH seria transporte, não um segundo roteador.
Nenhum arquivo do MRW foi alterado.

### Protótipo proposto, ainda não validado ponta a ponta

Antes do piloto, Renato deve aprovar explicitamente a habilitação no Dell-A.
Verificar localmente feature e serviço nas duas Dell antes de sugerir instalação
em ambas. Começar apenas no Dell-A: consultar registro e disponibilidade de
porta no destino, documentar alocação, limitar firewall ao IP do Lenovo e ao
perfil de rede adequado; usar conta sem privilégios administrativos e acesso
somente aos arquivos necessários. Não aplicar automaticamente a regra ampla
criada pela instalação. Não habilitar WinRM ou encaminhamento no roteador.

Provisionar chave pública para a conta aprovada; chave privada permanece no
Lenovo, protegida por passphrase, desbloqueada pelo usuário em agente quando
necessário. Conta comum usa authorized_keys no perfil; administradores têm
tratamento específico em administrators_authorized_keys e ACLs. Conferir a
impressão digital do host localmente/RDP e cadastrar known_hosts dedicado.
Não aceitar chave desconhecida automaticamente nem desativar sua validação.

Modelo de invocação após esses pré-requisitos (placeholders precisam ser
substituídos pelos valores verificados; não executado):

```powershell
ssh -F none -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=C:/CAMINHO/grid_known_hosts -o IdentitiesOnly=yes -i C:/CAMINHO/grid_key -o PreferredAuthentications=publickey -o ConnectTimeout=5 -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 USUARIO_APROVADO@192.168.15.116 "hostname"
```

Após hostname coincidir com DELL-RVF, substituir o comando remoto por uma
invocação explícita do Python instalado no Dell-A, com diretório/ambiente AIR
verificados: `python -m meizex_air.cli probe --detailed`. Confirmar shell
remoto e quoting de caminhos com espaços; não presumir que seja PowerShell.
Capturar stdout, stderr, código de saída e duração separadamente, com limite
global sugerido de 60 segundos no processo cliente. Só considerar sucesso
com código zero, saída válida e identidade/origem verificadas. Guardar snapshot
como evidência de laboratório, sem sobrescrever coletas aceitas automaticamente.

### Limitações e próximos testes

- **Rede:** recusa observada; timeout, queda durante execução e mudança de IP
  ainda não testados. Revalidar identidade antes de confiar em IP histórico.
- **Autenticação:** não exercitada. Testar chave ausente/incorreta e host key
  divergente; devem falhar sem prompt e sem execução.
- **Timeout:** ConnectTimeout limita conexão/handshake, não duração do comando.
  Encerrar cliente não garante término do processo remoto. Não repetir comando
  automaticamente após resultado ambíguo; cancelamento/idempotência ficam para
  proposta futura.
- **Saída grande:** não testada. Drenar stdout/stderr simultaneamente ou redirecionar
  ambos a arquivos; impor limite de armazenamento antes de uso contínuo. Validar
  Unicode e JSON no ambiente Windows. Não acumular saída ilimitada em memória.
- **Erros:** separar falha de transporte de erro do Python e ambiente ausente.
  Nenhum teste positivo de AIR sobre SSH foi possível nesta coleta.

Fontes primárias consultadas em 2026-09-07:
- [Microsoft: visão geral OpenSSH](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-overview)
- [Microsoft: autenticação por chave](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
- [Microsoft: configuração do servidor](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)
- [OpenSSH: opções do cliente](https://man.openbsd.org/ssh_config)

STATUS: investigação documentada; teste real de conexão falhou antes da
execução. Piloto autenticado pendente de aprovação e preparação do destino.
Sem promoção para DOT, sem integração MRW, sem habilitação SSH ou mudança de
segurança. NEXT-002 permanece parcialmente concluído.


### LAB-003 — Execução de NEXT-004: bloqueio de acesso inicial (2026-09-07)

NEXT-004 promoveu o piloto restrito ao Dell-A com aprovação registrada de
Renato. A missão MISSAO_CODEX_piloto_ssh_dell_a.md foi lida nesta rodada,
assim como o escopo completo de NEXT-004. A aprovação existe; o bloqueio é
operacional, não uma solicitação de nova aprovação do piloto.

Evidência de conectividade coletada em 2026-09-07T15:09:41.534764-03:00,
origem LAPTOP-CCJFHC8E: socket.create_connection para 192.168.15.116:22,
timeout de 3 segundos, retornou WinError 10061 (conexão recusada).
Isso não permite concluir se a feature está instalada ou disponível no Dell-A.

O primeiro passo exige consulta local no Dell-A. O shell desta sessão executa
no Lenovo; não há ferramenta de execução conectada ao Dell-A disponível e o
controle de aplicativos nativos/RDP está desabilitado nesta sessão. Acesso
RDP manual relatado anteriormente não equivale a acesso operável pelo agente.
Não foram tentados WinRM, tarefas remotas ou outro transporte alternativo.

Conforme a instrução da missão de parar e documentar quando um passo não for
possível, a execução parou antes de instalar/habilitar SSH, criar conta,
gerar chaves, alterar firewall ou reservar porta. Nenhuma configuração foi
alterada em qualquer nó. Não houve hostname remoto nem execução de AIR;
o critério de aceitação de NEXT-004 não foi cumprido.

Para retomar: disponibilizar uma sessão de execução local no Dell-A, ou o
usuário realizar e devolver a consulta inicial em PowerShell elevado no Dell-A:

```powershell
hostname
Get-WindowsCapability -Online -Name 'OpenSSH.Server*'
Get-Service -Name sshd -ErrorAction Continue
```

Esses comandos apenas consultam estado. Seu resultado libera o diagnóstico
do primeiro passo; a configuração subsequente ainda precisa de acesso local
operável e deve seguir todos os limites de NEXT-004. A passphrase da futura
chave deve ser informada pelo usuário localmente, não enviada ao chat.

STATUS: piloto aprovado, bloqueado no acesso inicial ao Dell-A; aguardando
acesso local/assistência do usuário. Nenhuma promoção para produção.
