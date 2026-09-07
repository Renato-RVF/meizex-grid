# MEIZEX AIR — Adaptive Intelligence Runtime

O MEIZEX AIR é um *runtime* adaptativo de inteligência que avalia tarefas, estado do sistema operacional, histórico empírico e disponibilidade de ferramentas para decidir dinamicamente se uma tarefa deve usar um método determinístico ou um modelo de IA e como executá-lo.

## Incrementos

* **AIR-001**: OBSERVE machine capability. Coleta das limitações de hardware nativo.
* **AIR-002**: OBSERVE effective live system state. Analisa "App Pressure" e calcula a "Effective Capacity" real.
* **AIR-003**: OBSERVE executor discovery. Descoberta read-only de mecanismos de execução efetivamente disponíveis.
* **AIR-004**: EXECUTION RESOURCE ONTOLOGY + CONTRACT FOUNDATION. Introdução da taxonomia de `ExecutionResource`.
* **AIR-005**: MODEL & ARTIFACT REGISTRY. Descoberta de artefatos de modelos (`.gguf`, `.safetensors`) em diretórios explicitamente governados (`AIR_MODEL_ROOTS`), e sua distinção ontológica: `Model Name != Model Artifact`.
* **AIR-006**: TASK SPECIFICATION & ACTIVE TASK FOUNDATION. Modelação determinística do que é a tarefa atual (`TaskLock`), garantindo estabilidade antes de avaliar qualquer *Formula Engineering*.
* **AIR-007**: FORMULA REGISTRY & VERSIONED FORMULA PROFILES. Modelagem estrita de Formulas como políticas de operação (`FormulaProfile`), distinguindo versionamento, *fingerprints* determinísticos e isolamento em *roots* autorizados.

**NÃO HÁ** carregamento autônomo de modelos nem orquestração ativa nos incrementos atuais. AIR IS A DECISION PLANE, NOT AN EXECUTION HARNESS.

## Princípio Fundamental: DECLARED != OBSERVED
Configuração em disco ou binários instalados no PATH não equivalem a endpoints acessíveis ou runtimes saudáveis em execução. O MEIZEX AIR observa o estado vivo da máquina.

## Instalação

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .
```

## Como Executar

Para exibir o snapshot estruturado:
```powershell
python -m meizex_air.cli probe
```

Para consultar estritamente a ontologia de Execution Resources (AIR-004):
```powershell
python -m meizex_air.cli resources
```

Para retrocompatibilidade do inventário de executores locais disponíveis (AIR-003):
```powershell
python -m meizex_air.cli executors
```

## Limitações Atuais
* Coleta de hardware, pressão e executores é estritamente de caráter diagnóstico.
* Endpoints locais de executores (ex: porta 11434 e 1234) são consultados apenas como verificação de saúde via rotas passivas (como `/api/version`). Nenhum prompt é enviado.
* O HarnessRouter/UHP são mapeados como adaptadores futuros. Nenhum UHP Client ativo foi criado.
