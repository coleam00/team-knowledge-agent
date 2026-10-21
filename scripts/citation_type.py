import argparse
from pathlib import Path

from app.retrieval import Citation

TARGET = Path(__file__).resolve().parents[1] / "web/src/citation.ts"


def citation_typescript() -> str:
    schema = Citation.model_json_schema(mode="serialization")
    lines = [
        "// Derived from app.retrieval.Citation; "
        "run uv run python -m scripts.citation_type.",
        "export interface Citation {",
    ]
    for name, field in schema["properties"].items():
        kind = field.get("type")
        if kind not in {"integer", "string"}:
            raise ValueError(f"Unsupported citation field schema: {name}")
        optional = "" if name in schema["required"] else "?"
        value_type = "number" if kind == "integer" else "string"
        lines.append(f"  {name}{optional}: {value_type}")
    return "\n".join([*lines, "}", ""])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    generated = citation_typescript()
    if arguments.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != generated:
            print("Citation type is stale: run uv run python -m scripts.citation_type")
            return 1
    else:
        TARGET.write_text(generated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
