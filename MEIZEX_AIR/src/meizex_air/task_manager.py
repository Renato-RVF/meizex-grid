import hashlib
import json
import datetime
from .models import TaskSpecification, TaskLock, ActiveTask, TaskStatus

def compute_task_fingerprint(spec: TaskSpecification) -> str:
    """
    Computes a deterministic SHA-256 fingerprint of the task's logical definition.
    It ignores serialization order by sorting keys and list elements where applicable.
    Changes to objective, scope, success conditions, etc. will change the fingerprint.
    """
    # Canonical representation
    canonical_data = {
        "title": spec.title,
        "objective": spec.objective,
        "task_class": spec.task_class.value,
        "scope": sorted(spec.scope),
        "out_of_scope": sorted(spec.out_of_scope),
        "success_conditions": sorted(spec.success_conditions),
        "termination_conditions": sorted(spec.termination_conditions),
        "required_capabilities": sorted([c.value for c in spec.required_capabilities]),
        "evidence_requirements": sorted(spec.evidence_requirements),
        "mutation_policy": spec.mutation_policy,
        "authority_requirement": spec.authority_requirement
    }
    
    # Dump to deterministic JSON
    json_str = json.dumps(canonical_data, sort_keys=True, separators=(',', ':'))
    
    # SHA-256
    return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

def create_task_lock(spec: TaskSpecification) -> TaskLock:
    """Creates a TaskLock from a TaskSpecification."""
    fingerprint = compute_task_fingerprint(spec)
    return TaskLock(
        task_id=spec.task_id,
        task_version=spec.task_version,
        task_fingerprint=fingerprint,
        objective=spec.objective,
        success_conditions=spec.success_conditions,
        out_of_scope=spec.out_of_scope
    )

def activate_task(spec: TaskSpecification) -> ActiveTask:
    """Creates an ActiveTask record from a specification."""
    lock = create_task_lock(spec)
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return ActiveTask(
        lock=lock,
        activated_at=now_utc,
        status=TaskStatus.ACTIVE,
        specification=spec
    )
