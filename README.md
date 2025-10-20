# pybrother

A Python CLI and helper library for printing labels on networked Brother P‑touch printers using their raster command set. 

## Quick Start

### Run without installing

```bash
# Show help
uvx pybrother -- --help

# Print a label
uvx pybrother -- "Hello World" --printer 192.168.1.100
```

`uvx` will download the package, create an isolated environment, and run the `pybrother` console entry point (an alias named `brother-printer` remains for compatibility).

### Install from PyPI

```bash
pip install pybrother

# Then use the CLI directly
pybrother "Hello World" --printer 192.168.1.100
```

## Command Options

The CLI supports the following useful switches:

- `text` – Label text (wrap in quotes for spaces)
- `--font` – Font size in dots (default: auto size per tape)
- `--tape` – Tape size (`W3_5`, `W6`, `W9`, `W12`, `W18`, `W24`)
- `--margin` – Left/right margin in pixels (default: `10`)
- `--copies` – Number of copies to print (default: `1`)
- `--printer` – Printer IP address (required unless discovered or from env var)
- `--listen` – Passively listen for printers via mDNS (requires `zeroconf`)
- `--listen-timeout` – Seconds to wait when listening (default: `70`)
- `--no-auto-detect` – Skip automatic tape detection

Set `BROTHER_PRINTER_IP` to avoid providing `--printer` each invocation.

## Development

```bash
# Install dependencies (including dev extras)
uv sync --extra dev

# Run the test suite
uv run pytest

# Build a wheel and sdist
uv run hatch build
```
