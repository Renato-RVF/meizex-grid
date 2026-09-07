import os
import json
import hashlib
from .models import FormulaProfile, FormulaRegistry
from .config import AIR_FORMULA_ROOTS

def compute_formula_fingerprint(formula: FormulaProfile) -> str:
    """
    Computes a deterministic SHA-256 fingerprint of the formula's logical definition.
    It ignores serialization order. Any change in operational policies or instructions
    will change the fingerprint.
    """
    canonical_data = {
        "formula_id": formula.formula_id,
        "display_name": formula.display_name,
        "description": formula.description,
        "intended_task_classes": sorted(formula.intended_task_classes),
        "intended_model_ids": sorted(formula.intended_model_ids),
        "intended_artifact_ids": sorted(formula.intended_artifact_ids),
        "intended_resource_kinds": sorted(formula.intended_resource_kinds),
        "system_instructions": formula.system_instructions,
        "task_policy": formula.task_policy,
        "grounding_policy": formula.grounding_policy,
        "search_policy": formula.search_policy,
        "stop_policy": formula.stop_policy,
        "recovery_policy": formula.recovery_policy,
        "context_policy": formula.context_policy,
        "tool_policy": formula.tool_policy,
        "output_policy": formula.output_policy,
        # keys inside dictionaries should be sorted by default in json.dumps with sort_keys=True
        "runtime_hints": formula.runtime_hints, 
        "declared_capabilities": sorted(formula.declared_capabilities)
    }
    
    json_str = json.dumps(canonical_data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

def discover_formulas() -> FormulaRegistry:
    """
    Discovers formulas inside the bounded AIR_FORMULA_ROOTS directories.
    Returns a FormulaRegistry containing the formulas and their fingerprints.
    """
    registry = FormulaRegistry()
    
    for root in AIR_FORMULA_ROOTS:
        if not os.path.exists(root) or not os.path.isdir(root):
            continue
            
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if filename.endswith(".json"):
                    full_path = os.path.join(dirpath, filename)
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        
                        # Just a fast sanity check to avoid parsing non-formula JSONs
                        if "formula_id" not in data or "formula_version" not in data:
                            continue
                            
                        formula = FormulaProfile(**data)
                        formula.local_path = full_path
                        formula.formula_fingerprint = compute_formula_fingerprint(formula)
                        registry.formulas.append(formula)
                    except Exception:
                        # Ignore unparseable or invalid formulas during discovery
                        pass
                        
    return registry
