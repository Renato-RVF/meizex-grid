import os
import hashlib
from typing import List, Dict, Optional
from pathlib import Path

from .config import AIR_MODEL_ROOTS
from .identity import artifact_identity
from .models import (
    ModelArtifact, ModelIdentity, ModelRegistry,
    ModelFormat, ModelSource, EvidenceConfidence,
    ArtifactIdentityStatus,
)

def compute_sha256(file_path: str) -> Optional[str]:
    """Computes full SHA-256 of a file. Memory efficient via chunks."""
    try:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096 * 1024), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return None

def extract_gguf_metadata_heuristic(filename: str) -> Dict[str, str]:
    """Extracts basic info from the filename (usually HuggingFace convention)."""
    # Example: gemma-2-9b-it-Q4_K_M.gguf
    # Example: Meta-Llama-3-8B-Instruct.Q5_K_M.gguf
    # Note: THIS IS A WEAK HEURISTIC, NOT A GGUF PARSER.
    metadata = {
        "quantization": None,
        "model_id": None
    }
    
    stem = Path(filename).stem
    # Tentativa de inferir quantizacao via sufixo comum
    parts = stem.replace(".", "-").split("-")
    for p in parts:
        pu = p.upper()
        if pu.startswith("Q") and ("_K_" in pu or pu in ["Q8_0", "Q4_0", "Q5_0", "IQ4_XS", "IQ3_M"]):
            metadata["quantization"] = pu
            break
            
    # Remove a quantizacao do nome para virar um model_id "base"
    base_name = stem
    if metadata["quantization"]:
        # Tenta remover a quantizacao
        q_str = metadata["quantization"]
        if f"-{q_str}" in base_name.upper():
            idx = base_name.upper().find(f"-{q_str}")
            base_name = base_name[:idx]
        elif f".{q_str}" in base_name.upper():
            idx = base_name.upper().find(f".{q_str}")
            base_name = base_name[:idx]
            
    metadata["model_id"] = base_name.lower().replace("_", "-")
    return metadata

def discover_artifacts(compute_hashes: bool = False) -> ModelRegistry:
    """Scans AIR_MODEL_ROOTS for local models."""
    
    artifacts = []
    
    # 1. Varredura explícita
    for root in AIR_MODEL_ROOTS:
        if not os.path.exists(root) or not os.path.isdir(root):
            continue
            
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                
                # Ignoramos arquivos que não parecem ser artefatos de modelos
                if ext not in [".gguf", ".safetensors", ".onnx"]:
                    continue
                    
                full_path = os.path.join(dirpath, filename)
                
                try:
                    stat = os.stat(full_path)
                    file_size = stat.st_size
                except Exception:
                    continue
                    
                # Identifica formato
                fmt = ModelFormat.UNKNOWN
                if ext == ".gguf":
                    fmt = ModelFormat.GGUF
                elif ext == ".safetensors":
                    fmt = ModelFormat.SAFETENSORS
                elif ext == ".onnx":
                    fmt = ModelFormat.ONNX
                    
                # Tenta heurística para extrair ID e Quantization
                meta = extract_gguf_metadata_heuristic(filename)
                
                model_id = meta.get("model_id") or filename.lower()
                quant = meta.get("quantization")
                
                # Para hashes pesados, evitamos no fast-probe.
                # CONTENT_ADDRESSED apenas quando SHA256 confiável existe;
                # caso contrário permanece PROVISIONAL_LOCATION_DERIVED
                # (fail-closed: nunca fabricar content identity).
                file_hash = None
                limitations = ["SHA-256 not computed (fast mode)"]
                identity_status = ArtifactIdentityStatus.PROVISIONAL_LOCATION_DERIVED
                if compute_hashes:
                    file_hash = compute_sha256(full_path)
                    if file_hash:
                        limitations.remove("SHA-256 not computed (fast mode)")
                        identity_status = ArtifactIdentityStatus.CONTENT_ADDRESSED

                artifact_id = (
                    artifact_identity(file_hash).id
                    if identity_status == ArtifactIdentityStatus.CONTENT_ADDRESSED
                    else f"art_{abs(hash(full_path))}"
                )

                artifact = ModelArtifact(
                    artifact_id=artifact_id,
                    model_id=model_id,
                    display_name=filename,
                    identity_status=identity_status,
                    source=ModelSource.LOCAL,
                    local_path=full_path,
                    filename=filename,
                    format=fmt,
                    quantization=quant,
                    file_size_bytes=file_size,
                    sha256=file_hash,
                    modified_at=stat.st_mtime,
                    evidence_sources=["FILESYSTEM_SCAN"],
                    confidence=EvidenceConfidence.CONFIRMED,
                    limitations=limitations
                )
                
                # Relações de compatibilidade hipotéticas
                if fmt == ModelFormat.GGUF:
                    artifact.compatible_resource_ids = ["llama_cpp_local", "lm_studio_local"]
                    artifact.limitations.append("Compatibility based on declared format, not empirical execution")
                
                artifacts.append(artifact)
                
    # 2. Agrupamento em Identities
    identities_map = {}
    for art in artifacts:
        mid = art.model_id
        if mid not in identities_map:
            identities_map[mid] = ModelIdentity(
                model_id=mid,
                display_name=mid,
            )
        identities_map[mid].artifacts.append(art)
        
    return ModelRegistry(identities=list(identities_map.values()))
