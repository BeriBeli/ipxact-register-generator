# Agent Guidelines for ipxact-register-generator

## Project Overview
A Python utility to convert register description spreadsheets into IP-XACT XML files. Uses JPype to interface with Java JAXB bindings for IP-XACT schema generation.

## Build Commands

### Manual Build Steps
```bash
# Java components
cd schema
mvn clean package
mvn dependency:copy-dependencies
cd ..

# Python environment
cd irgen
uv venv && uv sync --extra dev     # Recommended
# OR: python3 -m venv .venv && pip install -e .
cd ..
```

### Build Executable (Local)
```bash
cd irgen
uv pip install pyinstaller

# Linux/macOS
uv run pyinstaller -F src/irgen/main.py \
  --name irgen \
  --paths src \
  --add-data "../schema/target/ipxact-schema-1.0.0.jar:jar/" \
  --add-data "../schema/target/dependency/*:jar/dependency" \
  --exclude-module pytest \
  --exclude-module ruff \
  --exclude-module mypy \
  --console

# Windows (PowerShell)
uv run pyinstaller -F src/irgen/main.py `
  --name irgen `
  --paths src `
  --add-data "../schema/target/ipxact-schema-1.0.0.jar;jar/" `
  --add-data "../schema/target/dependency/*;jar/dependency" `
  --exclude-module pytest `
  --exclude-module ruff `
  --exclude-module mypy `
  --console
```

## CI/CD

项目使用GitHub Actions进行持续集成和持续部署：

- **CI**: 每次push/PR时自动运行测试、lint和type check（支持Python 3.10/3.11/3.12，Ubuntu/Windows/macOS）
- **CD**: 打tag时自动构建可执行文件并创建Release（支持三大平台）

工作流文件:
- `.github/workflows/ci.yml` - 持续集成
- `.github/workflows/cd.yml` - 持续部署

## Test Commands

### Run All Tests
```bash
cd irgen
pytest
```

### Run Single Test
```bash
cd irgen
pytest tests/test_parser.py::test_parse_n_series_range -v
pytest tests/test_parser.py -v
```

## Lint/Format Commands

### Ruff (Linting & Formatting)
```bash
cd irgen
ruff check src/            # Check for issues
ruff check --fix src/      # Auto-fix issues
ruff format src/           # Format code
```

### Type Checking
```bash
cd irgen
mypy src/
```

## Code Style Guidelines

### Python Version
- Python 3.10+ required
- Use modern syntax (match/case, union types with `|`)

### Imports
- Group imports: stdlib → third-party → local
- Use absolute imports for local modules: `from irgen.parser import ...`
- Local modules in `src/irgen/`

### Naming Conventions
- Functions/variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_CASE` (in config.py)
- Private functions: `_leading_underscore`

### Type Hints
- Use type hints for all function signatures
- Use `| None` instead of `Optional`
- Use `list[Any]` instead of `List[Any]` (Python 3.10+)
- Return type `-> None` for procedures

### Error Handling
- Use logging (not print): `logging.debug()`, `logging.info()`, `logging.warning()`, `logging.error()`, `logging.critical()`
- Catch specific exceptions, avoid bare `except:`
- Use `sys.exit(1)` for fatal errors
- Validate inputs early with descriptive error messages

### Polars DataFrame Operations
- Use expression API over loops where possible
- Chain operations with method chaining
- Use `with_columns()` for adding computed columns
- Use `forward_fill()` for filling nulls in grouped data

### Java Interop (JPype)
- Check `jpype.isJVMStarted()` before Java operations
- Use `jpype.JClass()` for importing Java classes
- Access Java methods with snake_case: `obj.setName()`
- Shutdown JVM in finally block: `jpype.shutdownJVM()`

### Project Structure
```
irgen/
  src/irgen/
    __init__.py
    __version__.py      # Version string
    main.py             # CLI entry point
    parser.py           # Excel parsing logic
    template.py         # Template generation
    config.py           # Constants
    attribute.py        # Register attribute mappings
    jpath.py            # JVM/Classpath utilities
  tests/
    test_parser.py      # Unit tests
```

### Dependencies
- Core: `fastexcel`, `jpype1`, `polars`, `pydantic`, `xlsxwriter`
- Dev: `mypy`, `pytest`, `ruff`, `pyinstaller`

### Key Patterns
- IP-XACT versions: "1685-2009", "1685-2014", "1685-2022"
- Register expansion syntax: `rega{n}, n=range(3)` or `n=0~2`
- Hex format: "0x" prefix required
- Reserved fields: skip fields matching `^(rsvd|reserved)\d*$`
