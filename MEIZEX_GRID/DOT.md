# DOT — Decisões estabelecidas do MEIZEX Grid

Autoridade protegida. Alterações aqui exigem supersessão explícita (ver
protocolo DOT/NEXT/LAB/ARC) — não editar diretamente para registrar progresso;
isso é papel do NEXT.

## DOT-001 — Interface desktop em Tkinter

A interface desktop do Grid será construída com Tkinter, seguindo a tecnologia
do app REDE (C:/PROJETOS/REDE/network_mapper.py). Operações de rede rodam fora
da thread da interface; resultados chegam à UI por fila e atualizações via
`after()`.

STATUS: aceito

## DOT-002 — MRW é o motor de execução

O MEIZEX Router Worker (MRW) é o motor de roteamento e execução do Grid. Antes
de criar coordenação, dispatch ou persistência adicionais, avaliar os
contratos já existentes no MRW.

STATUS: aceito

## DOT-003 — Ollama/LM Studio substituídos

Ollama foi desinstalado. LM Studio foi substituído por llama.cpp e runtimes
relacionados. Referências antigas a esses serviços em código ou documentação
não comprovam serviço ativo — precisam ser verificadas, não assumidas.

STATUS: aceito

## DOT-004 — REDE preservado como referência funcional

O app independente C:/PROJETOS/REDE é mantido como implementação de referência
para descoberta de rede (ping sweep + ARP + hostname + TTL), já comprovada em
uso. Não recriar essa lógica do zero sem necessidade.

STATUS: aceito

## DOT-005 — Identidade das três máquinas do Grid

Confirmado por observação real e simultânea de rede em 2026-09-06 (ver
[discovery.py](discovery.py), reaproveitando as funções do REDE):

| Máquina | IP | Hostname | RAM | CPU |
|---|---|---|---|---|
| Lenovo | 192.168.15.98 | LAPTOP-CCJFHC8E | 24GB | — |
| Dell-B | 192.168.15.42 | Dell-B | 32GB | i7-1355U (13th Gen) |
| Dell-A | 192.168.15.116 | DELL-RVF | 16GB | i7-1165G7 (11th Gen) |

Dell-A conecta via Wi-Fi (`VIVOFIBRA_EXT`); Lenovo e Dell-B via Ethernet/rede
cabeada ou Wi-Fi não confirmado individualmente. RDP nativo do Windows é
acessível do Lenovo para o Dell-B (testado pelo usuário) — candidato concreto
de transporte remoto para trabalho futuro, ainda não integrado a nada do Grid.

Um dispositivo só é considerado presente se respondeu ping OU apareceu na
tabela ARP local no momento da coleta — ausência de resposta não implica
máquina desligada.

STATUS: aceito

## DOT-007 — Distribuição de código: git remoto privado

Promovido de LAB-001 em 2026-09-06. O código do Grid (MEIZEX_GRID +
pacote meizex_air, não o monorepo `C:\PROJETOS` inteiro) é distribuído às
três máquinas via repositório git privado:

**https://github.com/Renato-RVF/meizex-grid**

O repositório é um subconjunto isolado, montado manualmente a partir do
monorepo local (que não tem remoto e não deve ser publicado inteiro — tem
material sensível misturado, ver MEIZEX_VAULT). Cada atualização relevante
do MEIZEX_GRID/MEIZEX_AIR precisa ser copiada para esse subconjunto e
commitada separadamente até existir um processo de sincronização automática
(não decidido ainda).

Fluxo para as Dell (Dell-A e Dell-B, que não têm o monorepo local):

```powershell
git clone https://github.com/Renato-RVF/meizex-grid.git
cd meizex-grid
# atualizações futuras:
git pull
```

Autenticação usa o fluxo padrão do Git (prompt de navegador na primeira vez),
sem necessidade de digitar senha em texto puro.

STATUS: aceito

## DOT-006 — Papel dos componentes do ecossistema no Grid

Encaixe investigado e aceito como direção, sem implementação de integração
ainda:

- **REDE** → descoberta de rede (ping/ARP/hostname), fonte da lógica em
  discovery.py.
- **AIR** → observação de capacidade por máquina (RAM efetiva, pressão,
  executores encontrados). Tem bug conhecido em
  `executor_probe.py:121` (discover_lm_studio confia em HTTP 200 sem checar
  processo) — corrigir antes de confiar nessa detecção específica.
- **GGUF Formula** → sugestão de configuração de inferência por máquina,
  a partir de um inventário de hardware fornecido (não descobre rede sozinho).
- **MRW** → decide e executa, consumindo os dados acima pelos contratos já
  existentes.
- **Governed Runner** → aceita/rejeita mudanças de código do próprio Grid
  (governança de desenvolvimento, não runtime de inferência).
- **JAMES** → disciplina de escopo/decisão (este protocolo).
- **MEIZEXLF-MCP** → ferramentas (fs, forensics, documentos) consumíveis pelo
  motor; hoje só via stdio local, transporte remoto ainda não definido.
- **MEIZEX_VAULT** → histórico/credenciais; não é infraestrutura de execução
  distribuída.

STATUS: aceito
