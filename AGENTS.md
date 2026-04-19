# Agent Guidelines for fritz_exporter

This document describes project conventions, architecture, and tooling for contributors and AI agents working on this repository.

## Project Overview

**fritz_exporter** is a Prometheus exporter for AVM Fritz! home network devices (routers, repeaters, etc.). It connects to Fritz! devices via the TR-064 API (using the `fritzconnection` Python library), discovers supported capabilities, and exposes metrics in Prometheus format.

- **Entrypoint**: `fritzexporter/__main__.py` → `main()`
- **Documentation**: [fritz-exporter.readthedocs.io](https://fritz-exporter.readthedocs.io)
- **Python version**: 3.11+

---

## Language and Typing

- The project is written in **Python 3.11+**.
- **Type annotations are required throughout** all production code. Every function, method, and class attribute must be annotated.
- Use `from __future__ import annotations` at the top of files to enable postponed evaluation of annotations (this is already the pattern in the codebase).
- Use `TYPE_CHECKING` guards for imports that are only needed for type hints to avoid circular imports:
  ```python
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from fritzexporter.fritzdevice import FritzDevice
  ```
- Avoid `Any` except where unavoidable (e.g., interfacing with untyped third-party libraries).
- **mypy** is used for static type checking. Configuration is in `pyproject.toml` under `[tool.mypy]`.

---

## Data Classes: attrs

This project uses the **`attrs`** library instead of Python's built-in `dataclasses`.

- Use `@define` from `attrs` for all data/config classes (not `@dataclass`).
- Use `attrs.field()` with `validator=`, `converter=`, and `factory=` arguments as needed.
- Use `attrs.validators` (e.g., `validators.instance_of`, `validators.min_len`, `validators.in_`) for field validation.
- Use `attrs.converters` (e.g., `converters.to_bool`) for field conversion.
- Validator methods on instances are defined as regular methods decorated with `@<field_name>.validator`.

Example from the codebase:
```python
@define
class DeviceConfig:
    hostname: str = field(validator=validators.min_len(1), converter=lambda x: str.lower(x))
    host_info: bool = field(default=False, converter=converters.to_bool)
```

---

## Linting: ruff

**ruff** is the linter. Run it with:
```bash
poetry run ruff check fritzexporter/
```

Key configuration (see `pyproject.toml` `[tool.ruff]`):
- **Line length**: 100 characters
- **Target**: Python 3.11
- **Tests and docs are excluded** from linting (`extend-exclude = ["tests", "docs"]`)
- A large set of rule groups is enabled (see `pyproject.toml` for the full list), including:
  - `E`, `W` — pycodestyle
  - `F` — pyflakes
  - `I` — isort (import ordering)
  - `N` — PEP8 naming
  - `ANN` — missing type annotations (all public functions/methods must be annotated)
  - `S` — bandit security checks
  - `B`, `BLE`, `FBT` — bugbear and anti-patterns (no boolean traps, no blind exceptions)
  - `UP` — pyupgrade (use modern Python syntax)
  - `T20` — no `print()` calls (use logging)
  - `PTH` — use `pathlib` instead of `os.path`
  - `PL` — pylint checks
  - `TRY` — try/except anti-patterns
  - `RUF` — ruff-specific rules
- **Ignored rules**: `E203`, `COM812`, `ISC001`, `ANN101`, `ANN102`, `ANN204`
- Docstring style convention: **Google** (`[tool.ruff.lint.pydocstyle] convention = "google"`)
- Use `# noqa: <CODE>` to suppress a specific rule where absolutely necessary; avoid blanket `# noqa`.

---

## Testing

Tests use **pytest**. Run with:
```bash
poetry run pytest
```

- Test files are in the `tests/` directory.
- Coverage is measured automatically (branch coverage, XML report). Config in `.coveragerc`.
- Mocking uses `unittest.mock` (`MagicMock`, `patch`).
- A shared `fc_services_mock.py` provides mock Fritz device service/action data for tests.
- Tests are excluded from ruff linting but should still be readable and well-structured.
- Use `pytest.mark` for test organization where appropriate.

---

## Commit Messages: Conventional Commits

This project uses **Conventional Commits** to drive automated versioning via the `release-please` GitHub Action.

**Format**: `<type>(<optional scope>): <description>`

Allowed commit types:
- `feat` — new feature (triggers minor version bump)
- `fix` — bug fix (triggers patch version bump)
- `perf` — performance improvement
- `refactor` — code refactoring
- `style` — code style changes
- `test` — test changes
- `build` — build system changes
- `ops` — operational/CI changes
- `docs` — documentation changes
- `merge` — merge commits

Breaking changes: add `BREAKING CHANGE:` in the commit body or append `!` after the type (e.g., `feat!: ...`) to trigger a major version bump.

**Always use conventional commits.** Non-conforming commit messages will be flagged and may not appear in the changelog.

---

## Architecture

### Package layout

```
fritzexporter/
  __init__.py           # Package version
  __main__.py           # CLI entrypoint and main loop
  fritzdevice.py        # FritzDevice, FritzCollector, FritzCredentials
  fritzcapabilities.py  # FritzCapability (ABC) + all concrete capability classes
  fritz_aha.py          # AHA (smart home) XML parsing
  action_blacklists.py  # TR-064 actions that must never be called
  exceptions.py         # Project-level exceptions
  config/
    __init__.py
    config.py           # ExporterConfig, DeviceConfig (attrs @define classes)
    exceptions.py       # Config-specific exceptions
```

### Core concepts

- **`FritzCapability`** (abstract base class in `fritzcapabilities.py`): Each subclass represents one group of metrics that a Fritz device may or may not support. Subclasses are auto-registered via `__init_subclass__`. Each capability:
  - Declares `requirements`: list of `(service, action)` tuples that must exist and be callable on the device.
  - Implements `create_metrics()` — instantiates Prometheus metric families.
  - Implements `_generate_metric_values(device)` — queries the device and populates metrics.
  - Implements `_get_metric_values()` — yields the populated metric families.

- **`FritzCapabilities`** (container): holds one instance of every `FritzCapability` subclass, keyed by class name. Supports merging across multiple devices.

- **`FritzDevice`**: connects to a single Fritz device, probes its capabilities, and holds a `FritzCapabilities` instance.

- **`FritzCollector`** (Prometheus `Collector`): holds multiple `FritzDevice` instances and implements `collect()` to yield all metrics.

- **Configuration** is loaded either from a YAML file or from environment variables. Config objects are `attrs` `@define` classes with validators.

### Adding a new metric/capability

1. Add a new subclass of `FritzCapability` in `fritzcapabilities.py`.
2. Set `self.requirements` in `__init__` with the required TR-064 `(service, action)` pairs.
3. Implement `create_metrics()`, `_generate_metric_values()`, and `_get_metric_values()`.
4. The subclass is automatically discovered — no registration needed.
5. Add corresponding tests in `tests/test_fritzcapabilities.py`, using the mock infrastructure in `tests/fc_services_mock.py`.

---

## Dependency Management

- Dependencies are managed with **Poetry** (`pyproject.toml`, `poetry.lock`).
- Install all dependencies: `poetry install`
- Add a dependency: `poetry add <package>`
- Do not update `poetry.lock` manually.

---

## CI / GitHub Actions

- **`run-tests.yaml`**: runs on PRs and pushes to `main`; executes linting (flake8, legacy) and pytest, uploads to SonarCloud.
- **`build-trunk.yaml`**: builds and publishes Docker images on pushes to `main`.
- **`release.yaml`**: triggered by `release-please`; handles versioning and releases.
- **`release-please`**: automatically opens release PRs based on conventional commit history. Do not manually bump version numbers.

---

## PR Review Checklist

For every pull request, review the following areas in addition to the Python code and tests. Update them as needed to reflect the changes introduced by the PR:

### Documentation (`docs/`)

- Check whether any user-facing behaviour, configuration options, CLI flags, metrics, or upgrade steps have changed.
- If so, update the relevant `.rst` files under `docs/` (e.g. `configuration.rst`, `running.rst`, `quickstart.rst`, `upgrading.rst`, `docker-images.rst`).
- Commit documentation changes with the `docs` type: `docs: ...`.

### Dockerfile

- Check whether the base Python image version, build dependencies, or the install/entrypoint commands need updating to match changes in `pyproject.toml`, `poetry.lock`, or the package itself.
- Verify that no unnecessary files are copied into the image and that the multi-stage build remains clean.
- Commit Dockerfile changes with the `build` type: `build: ...`.

### Helm chart (`helm/fritz-exporter/`)

- Check whether `Chart.yaml` (app version, chart version), `values.yaml` (default image tag, new config keys), or the templates under `helm/fritz-exporter/templates/` need updating to reflect new configuration options, environment variables, ports, or other changes.
- Follow semantic versioning for the chart version bump in `Chart.yaml`.
- Commit Helm chart changes with the `build` type: `build(helm): ...`.

---

## Style Notes

- Use `logging` for all output — never `print()` (enforced by ruff `T20`).
- Logger names follow the module hierarchy: `logging.getLogger("fritzexporter.<module>")`.
- Use `pathlib.Path` for file system operations, not `os.path`.
- Error messages passed to exceptions should be plain strings (not f-strings inline) to satisfy ruff `EM` rules.
- Use `collections.abc` types for abstract type hints (e.g., `collections.abc.Iterable`, `Generator`, `Iterator`).
- XML parsing must use `defusedxml` (not the standard `xml` library) for security.
