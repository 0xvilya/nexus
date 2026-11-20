"""Bounded metadata inspection. This is not a full tensor validator."""
import struct
from pathlib import Path


ARCHITECTURES = {'llama', 'qwen2'}
MODEL_DIMENSIONS = {
    'context_length', 'embedding_length', 'block_count', 'attention.head_count',
    'attention.head_count_kv', 'attention.key_length', 'attention.value_length',
    'expert_count', 'expert_used_count', 'expert_shared_count',
}
METADATA_KEYS = {
    'general.architecture', 'general.name', 'general.file_type', 'general.parameter_count',
} | {architecture + '.' + suffix for architecture in ARCHITECTURES for suffix in MODEL_DIMENSIONS}


def metadata(path: Path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        def read(n):
            data = stream.read(n)
            if len(data) != n:
                raise ValueError("Truncated GGUF")
            return data

        def number(fmt):
            return struct.unpack("<" + fmt, read(struct.calcsize("<" + fmt)))[0]

        def string():
            length = number("Q")
            if length > 1024 * 1024:
                raise ValueError("GGUF string limit exceeded")
            return read(length).decode("utf-8")

        fixed = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}

        def value(kind, depth=0):
            if kind in fixed:
                return number(fixed[kind])
            if kind == 8:
                return string()
            if kind == 9 and depth == 0:
                subtype, count = number("I"), number("Q")
                if count > 1_000_000:
                    raise ValueError("GGUF array limit exceeded")
                if subtype in fixed:
                    length = struct.calcsize("<" + fixed[subtype]) * count
                    if stream.tell() + length > size:
                        raise ValueError("Truncated GGUF array")
                    stream.seek(length, 1)
                else:
                    for _ in range(count):
                        value(subtype, depth + 1)
                return None
            raise ValueError("Unsupported GGUF metadata type")

        if read(4) != b"GGUF" or number("I") not in {2, 3}:
            raise ValueError("Expected GGUF version 2 or 3")
        tensors, count = number("Q"), number("Q")
        if count > 65536 or tensors > 100000:
            raise ValueError("GGUF metadata limits exceeded")
        result = {}
        for _ in range(count):
            key, kind = string(), number("I")
            item = value(kind)
            if key in METADATA_KEYS:
                if key in result:
                    raise ValueError("Duplicate GGUF model metadata key")
                result[key] = item
        if result.get("general.architecture") not in ARCHITECTURES:
            raise ValueError("Model architecture is not in the tested compatibility allowlist")
        return result
