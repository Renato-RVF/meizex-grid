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
