import argparse
import json
from pathlib import Path

from pydantic import RootModel

from app.ask import AskResult
from app.retrieval import Citation

TARGET = Path(__file__).resolve().parents[1] / "web/src/citation.ts"
ANSWER_TARGET = TARGET.with_name("answer.ts")


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


def field_type(schema: object) -> str:
    if not isinstance(schema, dict):
        raise ValueError("Expected a field schema")
    if "const" in schema:
        return json.dumps(schema["const"])
    if "$ref" in schema:
        return str(schema["$ref"]).removeprefix("#/$defs/")
    if schema.get("type") == "string":
        return "string"
    if schema.get("type") == "array":
        item = field_type(schema["items"])
        if schema.get("maxItems") == 0:
            return "readonly []"
        minimum = schema.get("minItems", 0)
        if not isinstance(minimum, int):
            raise ValueError("Expected an integer array minimum")
        if minimum:
            return "readonly [" + ", ".join([item] * minimum + [f"...{item}[]"]) + "]"
        return f"readonly {item}[]"
    raise ValueError(f"Unsupported answer field schema: {schema}")


def answer_typescript() -> str:
    schema = RootModel[AskResult].model_json_schema(mode="serialization")
    lines = [
        "// Derived from app.ask.AskResult; "
        "run uv run python -m scripts.citation_type.",
        "import type { Citation } from './citation'",
        "",
    ]
    union = schema["$defs"][field_type(schema)] if "$ref" in schema else schema
    members = [field_type(member) for member in union["anyOf"]]
    for name in members:
        definition = schema["$defs"][name]
        lines.append(f"export interface {name} {{")
        for field, value in definition["properties"].items():
            optional = "" if field in definition["required"] else "?"
            lines.append(f"  {field}{optional}: {field_type(value)}")
        lines.extend(["}", ""])
    lines.extend(["export type AnswerResponse = " + " | ".join(members), ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    for target, generated in (
        (TARGET, citation_typescript()),
        (ANSWER_TARGET, answer_typescript()),
    ):
        if arguments.check:
            if not target.exists() or target.read_text(encoding="utf-8") != generated:
                print(
                    f"{target.name} is stale: "
                    "run uv run python -m scripts.citation_type"
                )
                return 1
        else:
            target.write_text(generated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
