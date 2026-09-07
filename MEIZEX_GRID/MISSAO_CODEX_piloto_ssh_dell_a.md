# Missão para Codex (GPT-6 Astra) — Executar piloto de OpenSSH no Dell-A

## Aprovação

O usuário (Renato) aprovou explicitamente, em 2026-09-07, executar o piloto
de OpenSSH restrito ao Dell-A que você mesmo desenhou em LAB-003. Esta
missão autoriza a execução — leia `NEXT-004` em `NEXT.md` (recém-criado)
antes de agir: ele define escopo exato, o que está fora de escopo, e o
critério de conclusão. Siga esse escopo à risca, não o seu próprio roteiro
de LAB-003 isoladamente — o NEXT-004 é a versão com autoridade.

## Resumo do que fazer (ver NEXT-004 para o texto completo)

1. Verificar disponibilidade da feature/serviço OpenSSH no Dell-A.
2. Habilitar o serviço **só no Dell-A**.
3. Criar/usar conta sem privilégio administrativo dedicada a isso.
4. Autenticação só por chave pública (chave privada fica no Lenovo, com
   passphrase).
5. Firewall no Dell-A limitado ao IP do Lenovo (192.168.15.98).
6. Registrar a porta no `PORT_REGISTRY/localhost.md` **antes** de abrir de
   fato (você só consultou no LAB-003; agora precisa registrar).
7. Teste de aceitação: SSH autenticado do Lenovo até o Dell-A rodando
   `hostname` e depois `python -m meizex_air.cli probe --detailed` com
   sucesso.

## Limites (não fazer)

- Não habilitar em Lenovo ou Dell-B nesta rodada.
- Não habilitar WinRM.
- Não integrar com MRW/dispatch real ainda.
- Não criar automação que rode comandos remotos sem confirmação humana por
  execução.

## Ao concluir

Registre o resultado em LAB.md (nova entrada, ou atualização de LAB-003
citando a promoção para NEXT-004) e atualize o STATUS de NEXT-004 em
NEXT.md para "concluído" com a evidência (log da execução, saída do AIR).
Commit e push num commit descritivo, separado.

Se qualquer passo do escopo não for possível como descrito (ex.: a feature
OpenSSH não está disponível, ou falta permissão), pare e documente o
bloqueio em LAB.md em vez de improvisar um caminho alternativo fora do
escopo aprovado.
