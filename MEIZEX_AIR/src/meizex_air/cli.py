import argparse
import sys
import json
from .machine_probe import take_snapshot
from .executor_probe import discover_all_executors, discover_all_resources
from .artifact_probe import discover_artifacts
from .models import TaskSpecification, FormulaProfile
from .task_manager import compute_task_fingerprint, activate_task
from .formula_probe import discover_formulas, compute_formula_fingerprint

def main():
    parser = argparse.ArgumentParser(description="MEIZEX AIR - Machine Probe & Execution Resources")
    subparsers = parser.add_subparsers(dest="command")

    probe_parser = subparsers.add_parser("probe", help="Take an effective state snapshot")
    probe_parser.add_argument("--output", type=str, help="Output file path")
    probe_parser.add_argument("--detailed", action="store_true", help="Include more detailed metrics")

    exec_parser = subparsers.add_parser("executors", help="Print Executor Registry (AIR-003 Compatibility)")
    exec_parser.add_argument("--output", type=str, help="Output file path")
    
    res_parser = subparsers.add_parser("resources", help="Print Execution Resources Taxonomy (AIR-004)")
    res_parser.add_argument("--output", type=str, help="Output file path")

    art_parser = subparsers.add_parser("artifacts", help="Print Model Artifact Registry (AIR-005)")
    art_parser.add_argument("--output", type=str, help="Output file path")
    art_parser.add_argument("--hash", action="store_true", help="Compute full SHA-256 for artifacts")

    task_parser = subparsers.add_parser("task", help="Task Specification Operations (AIR-006)")
    task_subparsers = task_parser.add_subparsers(dest="task_cmd")
    
    validate_parser = task_subparsers.add_parser("validate", help="Validate a TaskSpecification JSON")
    validate_parser.add_argument("file", type=str, help="Path to task JSON")
    
    fingerprint_parser = task_subparsers.add_parser("fingerprint", help="Compute fingerprint of a TaskSpecification JSON")
    fingerprint_parser.add_argument("file", type=str, help="Path to task JSON")

    formulas_parser = subparsers.add_parser("formulas", help="List Formula Registry (AIR-007)")
    formulas_parser.add_argument("--output", type=str, help="Output file path")

    formula_parser = subparsers.add_parser("formula", help="Formula Profile Operations (AIR-007)")
    formula_subparsers = formula_parser.add_subparsers(dest="formula_cmd")
    
    formula_validate_parser = formula_subparsers.add_parser("validate", help="Validate a FormulaProfile JSON")
    formula_validate_parser.add_argument("file", type=str, help="Path to formula JSON")
    
    formula_fp_parser = formula_subparsers.add_parser("fingerprint", help="Compute fingerprint of a FormulaProfile JSON")
    formula_fp_parser.add_argument("file", type=str, help="Path to formula JSON")

    args = parser.parse_args()

    if args.command == "probe":
        try:
            snapshot = take_snapshot(detailed=args.detailed)
            json_str = snapshot.model_dump_json(indent=2)
            
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print(f"Snapshot salvo em {args.output}")
            else:
                print(json_str)
        except Exception as e:
            print(f"Erro ao coletar snapshot: {e}", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == "executors":
        try:
            executors = discover_all_executors()
            data = [e.model_dump() for e in executors]
            json_str = json.dumps(data, indent=2)
            
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print(f"Executor Registry salvo em {args.output}")
            else:
                print(json_str)
        except Exception as e:
            print(f"Erro ao investigar executores: {e}", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == "resources":
        try:
            resources = discover_all_resources()
            data = [r.model_dump() for r in resources]
            json_str = json.dumps(data, indent=2)
            
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print(f"Execution Resources salvo em {args.output}")
            else:
                print(json_str)
        except Exception as e:
            print(f"Erro ao investigar resources: {e}", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == "artifacts":
        try:
            registry = discover_artifacts(compute_hashes=args.hash)
            json_str = registry.model_dump_json(indent=2)
            
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(json_str)
                print(f"Artifacts Registry salvo em {args.output}")
            else:
                print(json_str)
        except Exception as e:
            print(f"Erro ao investigar artefatos: {e}", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == "task":
        if not args.task_cmd:
            task_parser.print_help()
            sys.exit(1)
            
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            spec = TaskSpecification(**data)
            
            if args.task_cmd == "validate":
                print(f"TaskSpecification '{spec.task_id}' (v{spec.task_version}) is VALID.")
            
            elif args.task_cmd == "fingerprint":
                fp = compute_task_fingerprint(spec)
                print(f"Task Fingerprint: {fp}")
                
        except Exception as e:
            print(f"Erro ao processar TaskSpecification: {e}", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == "formulas":
        try:
            registry = discover_formulas()
            json_out = registry.model_dump_json(indent=2)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(json_out)
                print(f"Formula Registry salvo em {args.output}")
            else:
                print(json_out)
        except Exception as e:
            print(f"Erro ao investigar formulas: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "formula":
        if not args.formula_cmd:
            formula_parser.print_help()
            sys.exit(1)
            
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            profile = FormulaProfile(**data)
            
            if args.formula_cmd == "validate":
                print(f"FormulaProfile '{profile.formula_id}' (v{profile.formula_version}) is VALID.")
            
            elif args.formula_cmd == "fingerprint":
                fp = compute_formula_fingerprint(profile)
                print(f"Formula Fingerprint: {fp}")
                
        except Exception as e:
            print(f"Erro ao processar FormulaProfile: {e}", file=sys.stderr)
            sys.exit(1)
            
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
