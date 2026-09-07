from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum
import datetime

class PressureLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"

class ProcessCategory(str, Enum):
    SYSTEM = "SYSTEM"
    USER_APP = "USER_APP"
    DEVELOPMENT = "DEVELOPMENT"
    BROWSER = "BROWSER"
    VIRTUALIZATION = "VIRTUALIZATION"
    AI_RUNTIME = "AI_RUNTIME"
    UNKNOWN = "UNKNOWN"

class EvidenceConfidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    NOT_FOUND = "NOT_FOUND"

class ResourceKind(str, Enum):
    DETERMINISTIC_EXECUTOR = "DETERMINISTIC_EXECUTOR"
    MODEL_RUNTIME = "MODEL_RUNTIME"
    RUNTIME_ENVIRONMENT = "RUNTIME_ENVIRONMENT"
    AGENT_HARNESS = "AGENT_HARNESS"
    HARNESS_GATEWAY = "HARNESS_GATEWAY"
    HUMAN = "HUMAN"
    UNKNOWN = "UNKNOWN"

class ResourceCapability(str, Enum):
    RUN_CODE = "RUN_CODE"
    RUN_SHELL = "RUN_SHELL"
    SERVE_MODEL = "SERVE_MODEL"
    INFERENCE = "INFERENCE"
    TOOL_ORCHESTRATION = "TOOL_ORCHESTRATION"
    FILE_MUTATION = "FILE_MUTATION"
    NETWORK_ACCESS = "NETWORK_ACCESS"
    STREAM_OUTPUT = "STREAM_OUTPUT"
    SESSION_STATE = "SESSION_STATE"

class RelationType(str, Enum):
    HOSTS = "HOSTS"
    USES = "USES"
    WRAPS = "WRAPS"
    DEPENDS_ON = "DEPENDS_ON"
    UNKNOWN_RELATION = "UNKNOWN_RELATION"

class ExecutorType(str, Enum):
    PYTHON = "PYTHON"
    POWERSHELL = "POWERSHELL"
    LLAMA_CPP = "LLAMA_CPP"
    LM_STUDIO = "LM_STUDIO"
    OLLAMA = "OLLAMA"
    ONNX_RUNTIME = "ONNX_RUNTIME"
    UNKNOWN = "UNKNOWN"

class ModelFormat(str, Enum):
    GGUF = "GGUF"
    SAFETENSORS = "SAFETENSORS"
    ONNX = "ONNX"
    MLX = "MLX"
    UNKNOWN = "UNKNOWN"

class ModelSource(str, Enum):
    LOCAL = "LOCAL"
    HUGGING_FACE = "HUGGING_FACE"
    OLLAMA_REGISTRY = "OLLAMA_REGISTRY"
    UNKNOWN = "UNKNOWN"

class ArtifactIdentityStatus(str, Enum):
    CONTENT_ADDRESSED = "CONTENT_ADDRESSED"
    PROVISIONAL_LOCATION_DERIVED = "PROVISIONAL_LOCATION_DERIVED"

# --- AIR-006 Task Taxonomies ---

class TaskClass(str, Enum):
    REPOSITORY_INSPECTION = "REPOSITORY_INSPECTION"
    DOCUMENT_ANALYSIS = "DOCUMENT_ANALYSIS"
    CODING_DIAGNOSIS = "CODING_DIAGNOSIS"
    STRUCTURED_EXTRACTION = "STRUCTURED_EXTRACTION"
    CLASSIFICATION = "CLASSIFICATION"
    SUMMARIZATION = "SUMMARIZATION"
    REASONING = "REASONING"
    SYSTEM_OBSERVATION = "SYSTEM_OBSERVATION"
    DETERMINISTIC_COMPUTATION = "DETERMINISTIC_COMPUTATION"
    UNKNOWN = "UNKNOWN"

class TaskStatus(str, Enum):
    DEFINED = "DEFINED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"

class TaskTerminationReason(str, Enum):
    SUPPORTED_CONCLUSION = "SUPPORTED_CONCLUSION"
    REASONABLE_EXHAUSTION = "REASONABLE_EXHAUSTION"
    SEARCH_BLOCKED = "SEARCH_BLOCKED"
    PREMATURE_TERMINATION = "PREMATURE_TERMINATION"
    UNKNOWN = "UNKNOWN"

# --- Hardware / OS / Pressure Data ---

class MachineInfo(BaseModel):
    hostname: Optional[str] = None
    architecture: Optional[str] = None

class OsInfo(BaseModel):
    name: Optional[str] = None
    version: Optional[str] = None

class CpuInfo(BaseModel):
    logical_processors: Optional[int] = None
    physical_cores: Optional[int] = None
    usage_percent: Optional[float] = None

class MemoryInfo(BaseModel):
    total_bytes: Optional[int] = None
    available_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    used_percent: Optional[float] = None
    committed_bytes: Optional[int] = None

class SwapInfo(BaseModel):
    total_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
    used_percent: Optional[float] = None

class StorageInfo(BaseModel):
    device: str
    mountpoint: str
    fstype: str
    total_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
    used_percent: Optional[float] = None

class ProcessInfo(BaseModel):
    pid: int
    name: str
    exe_path: Optional[str] = None
    username: Optional[str] = None
    create_time: Optional[float] = None
    num_threads: Optional[int] = None
    memory_bytes: Optional[int] = None
    cpu_percent: Optional[float] = None
    category: ProcessCategory = ProcessCategory.UNKNOWN

class AppGroupInfo(BaseModel):
    app_name: str
    category: ProcessCategory
    total_memory_bytes: int
    total_cpu_percent: float
    process_count: int

class ProcessPressure(BaseModel):
    total_processes: int
    top_memory: List[ProcessInfo]
    top_cpu: List[ProcessInfo]
    top_apps_by_memory: List[AppGroupInfo]

class PressureMetrics(BaseModel):
    memory_pressure: PressureLevel
    cpu_pressure: PressureLevel
    process_pressure: PressureLevel
    swap_pressure: PressureLevel

class EffectiveMemory(BaseModel):
    physical_total_bytes: int
    currently_available_bytes: int
    safety_reserve_bytes: int
    effective_safe_available_bytes: int

class EffectiveCapacity(BaseModel):
    memory: EffectiveMemory

# --- AIR-006 Task Definition ---

class TaskSpecification(BaseModel):
    task_id: str
    task_version: str
    title: str
    objective: str
    task_class: TaskClass = TaskClass.UNKNOWN
    
    scope: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    
    success_conditions: List[str] = Field(default_factory=list)
    termination_conditions: List[str] = Field(default_factory=list)
    
    required_capabilities: List[ResourceCapability] = Field(default_factory=list)
    evidence_requirements: List[str] = Field(default_factory=list)
    
    mutation_policy: str = "READ_ONLY"
    authority_requirement: str = "HUMAN_IN_THE_LOOP"
    created_at: Optional[str] = None

class TaskLock(BaseModel):
    task_id: str
    task_version: str
    task_fingerprint: str
    objective: str
    success_conditions: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)

class ActiveTask(BaseModel):
    lock: TaskLock
    activated_at: str
    status: TaskStatus = TaskStatus.DEFINED
    specification: TaskSpecification

# --- Execution Resources ---

class ResourceRelation(BaseModel):
    target_resource_id: str
    relation_type: RelationType
    relation_confidence: EvidenceConfidence

class ExecutionResource(BaseModel):
    resource_id: str
    resource_kind: ResourceKind = ResourceKind.UNKNOWN
    display_name: str
    
    declared_present: bool
    observed_present: bool
    confidence: EvidenceConfidence
    
    capabilities_declared: List[ResourceCapability] = Field(default_factory=list)
    capabilities_observed: List[ResourceCapability] = Field(default_factory=list)
    
    executable_path: Optional[str] = None
    version: Optional[str] = None
    
    process_running: bool = False
    process_ids: List[int] = Field(default_factory=list)
    
    endpoint_declared: Optional[str] = None
    endpoint_observed_reachable: bool = False
    
    detection_sources: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    
    relations: List[ResourceRelation] = Field(default_factory=list)
    last_verified_utc: Optional[str] = None

class ExecutorRecord(ExecutionResource):
    executor_id: str
    executor_type: ExecutorType

class ExecutionConstraints(BaseModel):
    memory: Optional[str] = None
    latency: Optional[str] = None
    risk: Optional[str] = None
    user_disruption: Optional[str] = None

class ExecutionContract(BaseModel):
    contract_version: str = "1.0"
    task_id: str
    task_version: str = "1"
    task_fingerprint: str = ""
    task_class: str
    
    selected_resource_id: str
    selected_resource_kind: ResourceKind
    
    requested_capabilities: List[ResourceCapability] = Field(default_factory=list)
    constraints: ExecutionConstraints = Field(default_factory=ExecutionConstraints)
    execution_parameters: Dict[str, Any] = Field(default_factory=dict)
    
    authority_requirement: str = "HUMAN_IN_THE_LOOP"
    verification_requirement: str = "DETERMINISTIC_TEST"
    evidence_requirement: str = "FULL_TRACE"

class ExecutionTraceMetadata(BaseModel):
    trace_id: str
    resource_id: str
    started_at: str
    ended_at: Optional[str] = None
    status: str = "PENDING"

class ModelArtifact(BaseModel):
    artifact_id: str
    model_id: str
    display_name: str

    identity_status: ArtifactIdentityStatus = ArtifactIdentityStatus.PROVISIONAL_LOCATION_DERIVED
    
    source: ModelSource = ModelSource.UNKNOWN
    source_repo: Optional[str] = None
    revision: Optional[str] = None
    
    local_path: str
    filename: str
    
    format: ModelFormat = ModelFormat.UNKNOWN
    quantization: Optional[str] = None
    
    file_size_bytes: int
    sha256: Optional[str] = None
    
    architecture: Optional[str] = None
    parameter_count: Optional[int] = None
    context_declared: Optional[int] = None
    
    created_at: Optional[float] = None
    modified_at: Optional[float] = None
    
    declared_capabilities: List[str] = Field(default_factory=list)
    observed_capabilities: List[str] = Field(default_factory=list)
    
    compatible_resource_ids: List[str] = Field(default_factory=list)
    
    evidence_sources: List[str] = Field(default_factory=list)
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN
    limitations: List[str] = Field(default_factory=list)

class ModelIdentity(BaseModel):
    model_id: str
    display_name: str
    provider: Optional[str] = None
    family: Optional[str] = None
    artifacts: List[ModelArtifact] = Field(default_factory=list)

class ModelRegistry(BaseModel):
    identities: List[ModelIdentity] = Field(default_factory=list)

# --- AIR-007 Formula Registry ---

class FormulaSource(str, Enum):
    LOCAL = "LOCAL"
    MEIZEX = "MEIZEX"
    IMPORTED = "IMPORTED"
    EXPERIMENTAL = "EXPERIMENTAL"
    UNKNOWN = "UNKNOWN"

class FormulaProfile(BaseModel):
    formula_id: str
    formula_version: str
    display_name: str
    description: str
    
    intended_task_classes: List[str] = Field(default_factory=list)
    intended_model_ids: List[str] = Field(default_factory=list)
    intended_artifact_ids: List[str] = Field(default_factory=list)
    intended_resource_kinds: List[str] = Field(default_factory=list)
    
    system_instructions: Optional[str] = None
    
    task_policy: Optional[str] = None
    grounding_policy: Optional[str] = None
    search_policy: Optional[str] = None
    stop_policy: Optional[str] = None
    recovery_policy: Optional[str] = None
    context_policy: Optional[str] = None
    tool_policy: Optional[str] = None
    output_policy: Optional[str] = None
    
    runtime_hints: Dict[str, Any] = Field(default_factory=dict)
    
    declared_capabilities: List[str] = Field(default_factory=list)
    observed_capabilities: List[str] = Field(default_factory=list)
    
    created_at: Optional[str] = None
    source: FormulaSource = FormulaSource.UNKNOWN
    limitations: List[str] = Field(default_factory=list)
    
    formula_fingerprint: Optional[str] = None
    local_path: Optional[str] = None

class FormulaRegistry(BaseModel):
    formulas: List[FormulaProfile] = Field(default_factory=list)

class MachineSnapshot(BaseModel):
    schema_version: str = "0.7"
    timestamp_utc: str
    machine: MachineInfo
    os: OsInfo
    cpu: CpuInfo
    memory: MemoryInfo
    swap: SwapInfo
    storage: List[StorageInfo]
    gpu: List[dict] = Field(default_factory=list)
    process_pressure: ProcessPressure
    pressure_metrics: PressureMetrics
    effective_capacity: EffectiveCapacity
    resources: List[ExecutionResource] = Field(default_factory=list)
    executors: List[ExecutorRecord] = Field(default_factory=list)
    model_registry: ModelRegistry = Field(default_factory=ModelRegistry)
    formula_registry: FormulaRegistry = Field(default_factory=FormulaRegistry)

