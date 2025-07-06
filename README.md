# Brother Label Printer

A small Python library and CLI tool for printing labels to Brother printers. This project was largely developed with AI assistance.

## Usage

### Install

```bash
uv sync
```

### Print a label

```bash
# Basic usage (auto-detects printer and tape size)
uv run brother_printer.py "Hello World"

# Specify font size and tape
uv run brother_printer.py "Large Text" --font 60 --tape W12

# Use white tape with black text
uv run brother_printer.py "White Label" --white-tape

# Manual printer IP
uv run brother_printer.py "Test" --printer 192.168.1.100

# Or set environment variable for default IP
export BROTHER_PRINTER_IP=192.168.1.100
uv run brother_printer.py "Test"
```

### Options

- `--font` - Font size in pixels (default: 40)
- `--tape` - Tape size: W3_5, W6, W9, W12, W18, W24 (auto-detected)
- `--margin` - Left/right margins in pixels (default: 10)
- `--copies` - Number of copies (default: 1)
- `--printer` - Printer IP address (auto-discovered or from BROTHER_PRINTER_IP env var)
- `--white-tape` - Use white tape with black text
- `--mode` - Use `png` (default) or `labelprinterkit` mode

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run tests
uv run pytest
```