"""Allow ``python -m pybrother`` to run the CLI."""

from .cli import main

if __name__ == "__main__":  # pragma: no cover - direct CLI execution path
    main()
