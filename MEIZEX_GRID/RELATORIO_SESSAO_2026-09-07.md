# Relatório final — MEIZEX Grid, sessão de 2026-09-06/07

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

## Limitação conhecida, documentada e não escondida

O roteador de missão do MRW (`capabilities/router.py`) só modela duas
categorias de recurso: **local** ou **cloud**. Não existe uma terceira
categoria para "rede confiável, máquina remota" — por isso a seleção
automática de quando usar o Grid, a partir do texto de uma missão, **não
foi implementada**. Isso exigiria estender o modelo de dados
(`CapabilityResource.local: bool` → algo como `location: Literal["local",
"grid","cloud"]`), uma decisão de arquitetura, não uma linha de código
apressada. O caminho está documentado em NEXT-007 para quando isso for
decidido.

## Estado final do Grid

- Três máquinas identificadas, com capacidade real medida.
- Código sincronizado via git privado entre as três.
- Execução remota real e testada nas duas Dell via SSH, com conta
  restrita e firewall travado.
- MRW com um caminho real (não teórico) de execução remota, com
  roteamento por recurso funcionando na camada de despacho.
- Lacuna de design conhecida e registrada para a seleção automática por
  missão — não é um "TODO" vago, é uma decisão específica com o porquê
  explicado.

## Onde encontrar tudo

- Repositório: https://github.com/Renato-RVF/meizex-grid
- Decisões aceitas: `MEIZEX_GRID/DOT.md`
- Trabalho autorizado e resultado de cada item: `MEIZEX_GRID/NEXT.md`
  (NEXT-001 a NEXT-007)
- Hipóteses e investigações: `MEIZEX_GRID/LAB.md`
- Evidências de capacidade e execução remota: `MEIZEX_GRID/air_snapshots/`
- Testes reais de integração MRW: `MEIZEX_GRID/test_remote_ssh_executor.py`
  e `MEIZEX_GRID/test_dispatcher_remote_ssh_routing.py`
