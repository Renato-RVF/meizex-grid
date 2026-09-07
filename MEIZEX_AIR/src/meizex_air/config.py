import os

SAFETY_RESERVE_BYTES = 2 * 1024 * 1024 * 1024

APP_CATEGORIES = {
    "chrome.exe": "BROWSER",
    "msedge.exe": "BROWSER",
    "firefox.exe": "BROWSER",
    "python.exe": "DEVELOPMENT",
    "code.exe": "DEVELOPMENT",
    "pycharm64.exe": "DEVELOPMENT",
    "docker.exe": "VIRTUALIZATION",
    "wslservice.exe": "VIRTUALIZATION",
    "svchost.exe": "SYSTEM",
    "explorer.exe": "SYSTEM",
    "wininit.exe": "SYSTEM",
    "services.exe": "SYSTEM",
    "llama-server.exe": "AI_RUNTIME",
    "ollama.exe": "AI_RUNTIME",
}

class PressureThresholds:
    MEMORY_LOW = 60.0
    MEMORY_MODERATE = 80.0
    MEMORY_HIGH = 90.0
    
    CPU_LOW = 50.0
    CPU_MODERATE = 75.0
    CPU_HIGH = 90.0
    
    SWAP_LOW = 20.0
    SWAP_MODERATE = 50.0
    SWAP_HIGH = 80.0
    
    PROCESS_COUNT_LOW = 150
    PROCESS_COUNT_MODERATE = 250
    PROCESS_COUNT_HIGH = 500

AIR_MODEL_ROOTS = []

if "AIR_MODEL_ROOTS" in os.environ:
    AIR_MODEL_ROOTS.extend(os.environ["AIR_MODEL_ROOTS"].split(";"))

lm_studio_default = os.path.expanduser("~/.cache/lm-studio/models")
if os.path.exists(lm_studio_default):
    AIR_MODEL_ROOTS.append(lm_studio_default)
    
ollama_default = os.path.expanduser("~/.ollama/models/blobs")
if os.path.exists(ollama_default):
    AIR_MODEL_ROOTS.append(ollama_default)

AIR_FORMULA_ROOTS = []
if "AIR_FORMULA_ROOTS" in os.environ:
    AIR_FORMULA_ROOTS.extend(os.environ["AIR_FORMULA_ROOTS"].split(";"))

default_formulas = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "formulas"))
if os.path.exists(default_formulas):
    AIR_FORMULA_ROOTS.append(default_formulas)

# AIR-BEW-002: repo-root-level state directory for deterministic persistence
# (TaskPlan/BoundedExecutionSprint/SprintCheckpoint) - same env-var-override
# convention as AIR_MODEL_ROOTS/AIR_FORMULA_ROOTS above. Not created eagerly;
# BoundedExecutionStore creates it (and subdirectories) on first write.
AIR_STATE_ROOT = os.environ.get(
    "AIR_STATE_ROOT",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "state")),
)