import argparse

import uvicorn


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535")
    return port


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=port_number, required=True)
    arguments = parser.parse_args()
    uvicorn.run(
        "app.main:create_app", factory=True, host="127.0.0.1", port=arguments.port
    )


if __name__ == "__main__":
    main()
