# Agent Guidelines for fritz_exporter

This document describes project conventions, architecture, and tooling for contributors and AI agents working on this repository.

## Project Overview

**fritz_exporter** is a Prometheus exporter for AVM Fritz! home network devices (routers, repeaters, etc.). It connects to Fritz! devices via the TR-064 API (using the `fritzconnection` Python library), discovers supported capabilities, and exposes metrics in Prometheus format.

- **Entrypoint**: `fritzexporter/__main__.py` → `main()`
- **Documentation**: [fritz-exporter.readthedocs.io](https://fritz-exporter.readthedocs.io)
- **Python version**: 3.14+

---

## Language and Typing

- The project is written in **Python 3.14+**.
- **Type annotations are required throughout** all production code. Every function, method, and class attribute must be annotated.
- Use `from __future__ import annotations` at the top of files to enable postponed evaluation of annotations (this is already the pattern in the codebase).
- Use `TYPE_CHECKING` guards for imports that are only needed for type hints to avoid circular imports:
  ```python
  from typing import TYPE_CHECKING
  if TYPE_CHECKING:
      from fritzexporter.fritzdevice import FritzDevice
  ```
- Avoid `Any` except where unavoidable (e.g., interfacing with untyped third-party libraries).
- **ty** is used for static type checking. Configuration is in `pyproject.toml` under `[tool.ty]`.

---

## Data Classes: attrs

This project uses the **`attrs`** library instead of Python's built-in `dataclasses`.

- Use `@define` from `attrs` for all data/config classes (not `@dataclass`).
- Use `attrs.field()` with `validator=`, `converter=`, and `factory=` arguments as needed.
- Use `attrs.validators` (e.g., `validators.instance_of`, `validators.min_len`, `validators.in_`) for field validation.
- Use `attrs.converters` (e.g., `converters.to_bool`) for field conversion.
- Validator methods on instances are defined as regular methods decorated with `@<field_name>.validator`.

### Using attrs for input validation

attrs validators and converters are the **primary defence against bad config input**. Both the YAML file path and the env var path ultimately construct the same `DeviceConfig` / `ExporterConfig` objects, so validation placed on the attrs field fires for both paths automatically — no need to validate in two places.

**The pattern:**
1. **Convert first, then validate.** Converters run before validators. Use a converter to coerce raw input (e.g. env var strings) to the right type, then use a validator to check the value is in range.
2. **Use built-in validators where possible** — they compose cleanly and produce clear error messages:
   - `validators.instance_of(T)` — type check
   - `validators.min_len(n)` / `validators.max_len(n)` — length bounds
   - `validators.ge(n)` / `validators.le(n)` — numeric bounds
   - `validators.in_(collection)` — allowlist
   - `validators.optional(v)` — applies `v` only when the value is not `None`
   - `validators.and_(v1, v2, ...)` — combine multiple validators
3. **Write a custom `@<field>.validator` method** only when the logic cannot be expressed with built-ins (e.g. cross-field checks, file existence).

**Converter pattern for optional typed fields** (e.g. `connection_timeout`):
```python
DEFAULT_CONNECTION_TIMEOUT: int = 10

def _convert_optional_int(value: int | str | None) -> int | None:
    if value is None:
        return None
    timeout = int(value)       # coerce str from env vars
    if timeout == 0:
        return None            # treat 0 as "no value" when appropriate
    return timeout

@define
class DeviceConfig:
    connection_timeout: int | None = field(
        default=DEFAULT_CONNECTION_TIMEOUT,
        converter=_convert_optional_int,
        validator=validators.optional(validators.ge(1)),
    )
```

An omitted YAML value uses the 10-second default. This converter handles explicit `None` or `0`
(no timeout), `"15"` (string from env var), and rejects negatives — all in one place.

**Custom validator method pattern** (for logic that can't use built-ins):
```python
@define
class DeviceConfig:
    password: str | None = field(default=None)

    @password.validator
    def check_password(self, _: attrs.Attribute, value: str | None) -> None:
        if value is not None and len(value) > FRITZ_MAX_PASSWORD_LENGTH:
            raise FritzPasswordTooLongError
```

**Key rule:** if you add a new config field, always add a converter and/or validator on the attrs field itself. Do not validate the raw dict in `from_config()` or in `_read_config_from_env()` — that duplicates logic and is easy to miss in one of the two config paths.

---

## Linting: ruff

**ruff** is the linter. Run it with:
```bash
uv run ruff check fritzexporter/
```

Key configuration (see `pyproject.toml` `[tool.ruff]`):
- **Line length**: 100 characters
- **Target**: Python 3.14
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
- **Ignored rules**: `E203`, `COM812`, `ISC001`, `ANN204`
- Docstring style convention: **Google** (`[tool.ruff.lint.pydocstyle] convention = "google"`)
- Use `# noqa: <CODE>` to suppress a specific rule where absolutely necessary; avoid blanket `# noqa`.
- **Follow the boy scout rule.** Do not fix lint in unrelated code. If you are already touching a line or block, clean up lint issues there so you leave it cleaner than you found it.
- **Adhere to the ruff rules in production code.** Treat `pyproject.toml` as the source of truth for exact lint boundaries, and keep `fritzexporter/` free of new `E501` violations.
- **Tests and docs stay more flexible.** They are excluded from ruff, but should still remain reasonably readable.

---

## Testing

Tests use **pytest**. Run with:
```bash
uv run pytest
```

- Test files are in the `tests/` directory.
- Coverage is measured automatically (branch coverage, XML report). Config in `pyproject.toml` under `[tool.coverage]`.
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
  fritzdevice.py        # FritzDevice, FritzCollector, DeviceSpec, FritzCredentials
  fritzcapabilities.py  # FritzCapability (ABC) + all concrete capability classes
  fritz_aha.py          # AHA (smart home) XML parsing
  fritz_webui.py        # Generic Fritz!Box web login + data.lua/REST API/AHA client
  fritz_docsis.py       # DOCSIS cable channel types + parser (web UI page)
  fritz_rest_generic.py # Technology-agnostic /api/v0 REST parsers (connections, segments)
  fritz_fiber.py        # Fibre optical level parser (fiberStats web UI page)
  tr064_remote.py       # TR-064/web UI ports, WAN remote TR-064 (/tr064) rewriting
  reply.py              # Typed reading of device replies (Reply, read_action, Issues, DataError)
  exceptions.py         # Project-level exceptions
  config/
    __init__.py
    config.py           # ExporterConfig, DeviceConfig (attrs @define classes)
    exceptions.py       # Config-specific exceptions
```

### Core concepts

- **`FritzCapability`** (abstract base class in `fritzcapabilities.py`): Each subclass represents one group of metrics that a Fritz device may or may not support. Subclasses are auto-registered via `__init_subclass__`. Each capability:
  - Declares `requirements`: list of `(service, action)` tuples that must exist and be callable on the device.
  - Implements `create_metrics()` — creates its metric families with `self._gauge(key, name, help, *labels, unit=...)` / `self._counter(...)` (labels `serial`, `friendly_name` come first).
  - Implements `_generate_metric_values(device)` — queries the device and populates metrics. Tables read by index go through `self._enumerate(...)`.
  - Sets `uses_webui = True` if it reads from the web interface, and gets the client with `self._webui(device)`.
  - Overrides `enabled(device)` if a config option switches it on (`host_info`, `wifi_client_info`), and `_probe()` if a requirement needs arguments to be called.

- **`FritzCapabilities`** (container): holds one instance of every `FritzCapability` subclass, keyed by class name. Each device has its own (with `present` flags and the current read problem per capability); the collector has one without a device that drives the collect loop.

- **`FritzDevice`**: connects to a single Fritz device, probes its capabilities, and holds a `FritzCapabilities` instance.

- **`FritzCollector`** (Prometheus `Collector`): holds multiple `FritzDevice` instances (and offline devices it retries) and implements `collect()` to yield all metrics, plus the self-monitoring metrics (`fritz_scrapes_*`, `fritz_device_reachable`, `fritz_read_problem`).

- **Configuration** is loaded either from a YAML file or from environment variables. Config objects are `attrs` `@define` classes with validators. See the **Dual Configuration** section below for the complete mapping and the rule that both paths must stay in sync.

### Reading device data

**Never index raw device data** (`result["NewFoo"]`, `raw.get("data", {})`). Read every value through `fritzexporter/reply.py`:

- `read_action(device, service, action, **arguments)` returns a `Reply`; `Reply(raw, "source")` wraps JSON objects.
- `num()`, `integer()`, `flag()`, `text(key, default)` for strict reads; `opt_*` for optional ones; `opt_obj()`, `items()` and `issues.objects()` for nesting; `to_float`/`to_int`/`to_bool`/`to_str` for single values.
- Rule: missing/`None`/`""`/`[]`/`{}` is absent; present but wrong type raises `DataError`. `DataError` messages name the field, never the value.

**Keep failures at the level they affect** (see "Reading Device Data" in `docs/coding.rst`):

- Value: `self._add(key, labels, self.issues.num(reply, field))` — a defective value costs only its sample.
- Item (host, channel, WLAN band, ...): `with self._item("host 3"):` — a defective label costs only that item.
- Capability: strict reads outside an item — use them only for what the whole capability depends on (shared labels, loop counts, reply structure).
- Device: an unreadable serial raises `FritzDeviceSerialUnknownError`; the device is registered offline and given up after `SERIAL_RETRIES` (3) retries.

`FritzCapability.get_metrics` handles the rest centrally: it exports `fritz_read_problem{capability, level}` while a problem lasts and logs it when it appears, hourly while it lasts and when it is gone (`report_problem()`; also used for the connection mode). An error reply to a TR-064 request skips the item (inside `_item`) or the capability; only a device that does not answer (`TRANSPORT_ERRORS`) is unreachable. Other errors are treated as bugs (traceback on every scrape). **Do not add `try/except` in a capability just to log and return** — only where it can do better than skipping (e.g. a fallback).

### Adding a new metric/capability

1. Add a new subclass of `FritzCapability` in `fritzcapabilities.py`.
2. Set `self.requirements` in `__init__` with the required TR-064 `(service, action)` pairs.
3. Implement `create_metrics()` and `_generate_metric_values()` (reading data as described above).
4. The subclass is automatically discovered — no registration needed.
5. Add corresponding tests in `tests/test_fritzcapabilities.py`, using the mock infrastructure in `tests/fc_services_mock.py`.
6. Show the new metrics in the overview dashboard: edit `misc/grafana/build_dashboards.py`, then run `uv run python misc/grafana/build_dashboards.py`. Never edit the JSON. `tests/test_grafana_dashboards.py` fails while a metric is missing from the overview or the JSON is stale.

### Naming new metrics

New metrics follow the [Prometheus naming rules](https://prometheus.io/docs/practices/naming/):

- The unit goes into the name as a plural suffix (set it with `unit=`), in base units: `seconds`, `bytes` (also for bits), `meters`, `volts`, `amperes`, `celsius`. Logarithmic units without a base unit (`db`, `dbm`) are written in lowercase.
- Exception: where a domain has its own established unit, use it. Household energy is read in watt-hours (on the meter, the bill and the Fritz!Box), so energy is exported in `watt_hours`, not joules.
- Counters end in `_total`; rates are `…_per_second`; ratios are 0–1 with the suffix `_ratio`, not percent.
- `count` is not a unit, and `_count`/`_sum`/`_bucket` are reserved for histograms and summaries.
- Labels of a data series only name *what* is measured (the device, a band, a MAC address). Anything that can change while that thing stays the same (versions, addresses, names, states, access types) goes into a separate `…_info` metric with value `1`, keyed by the same labels; otherwise every change starts a new series (and resets counters).

**Existing metric names are not renamed.** Many predate these rules (`…_kbps`, `…_percent`, `…_count`, `…_C`), but renaming them would break users' dashboards and alerts for no functional gain (decided for 4.0, #744). Do not "fix" them in passing.

---

## Dual Configuration: File vs. Environment Variables

The exporter supports two **mutually exclusive** configuration methods, both implemented in `fritzexporter/config/config.py`. Exactly one is active at runtime — there is no merging or layering:

- **Config file** (`--config` flag): YAML, supports multiple devices. Used when a config file path is provided.
- **Environment variables**: single-device only. Used when **no** config file is passed.

If a config file path is given, environment variables are ignored entirely, and vice versa.

Both paths produce the same `ExporterConfig` / `DeviceConfig` attrs objects and go through the same validators. The env path (`_read_config_from_env`) assembles a plain `dict` in the same shape as a parsed YAML file, then hands it to the same `from_config` class methods.

### Mapping: YAML key → environment variable

| Scope | YAML key | Environment variable | Default |
|---|---|---|---|
| Exporter | `exporter_port` | `FRITZ_PORT` | `9787` |
| Exporter | `log_level` | `FRITZ_LOG_LEVEL` | `INFO` |
| Exporter | `listen_address` | `FRITZ_LISTEN_ADDRESS` | `127.0.0.1` |
| Device | `hostname` | `FRITZ_HOSTNAME` | `fritz.box` |
| Device | `name` | `FRITZ_NAME` | *(the hostname; names must be unique)* |
| Device | `username` | `FRITZ_USERNAME` | *(required)* |
| Device | `password` | `FRITZ_PASSWORD` | *(required unless password_file set)* |
| Device | `password_file` | `FRITZ_PASSWORD_FILE` | *(required unless password set)* |
| Device | `host_info` | `FRITZ_HOST_INFO` | `False` |
| Device | `wifi_client_info` | `FRITZ_WIFI_CLIENT_INFO` | `False` |
| Device | `connection_timeout` | `FRITZ_CONNECTION_TIMEOUT` | `10` (seconds; `0` disables timeout) |
| Device | `use_tls` | `FRITZ_USE_TLS` | `False` |
| Device | `port` | `FRITZ_DEVICE_PORT` | *(none — local: TR-064 on 49000 / TLS 49443, web UI on 80 / 443; remote_access: 443 for both)* |
| Device | `remote_access` | `FRITZ_REMOTE_ACCESS` | `False` |
| Device | `allow_unsafe_md5_password` | `FRITZ_ALLOW_UNSAFE_MD5_PASSWORD` | `False` |

### Rule: keep both paths in sync

**Whenever you add or change a device or exporter config option, you must update both paths:**

1. Add the field to `DeviceConfig` or `ExporterConfig` (with converter + validator as appropriate).
2. Add the YAML key read in `DeviceConfig.from_config()` / `ExporterConfig.from_config()`.
3. Add the corresponding `os.getenv(...)` read in `_read_config_from_env()` and include it in the device/config dict.
4. Add tests for both paths in `tests/test_config.py` (see `TestFileConfigs` and `TestEnvConfig`).
5. Update `docs/configuration.rst`: the env var table and the YAML example block.

Failure to keep the two paths in sync silently degrades the env-based deployment path, which is the primary path for Docker/container users.

---



- Dependencies are managed with **uv** (`pyproject.toml`, `uv.lock`).
- Install all dependencies: `uv sync`
- Add a dependency: `uv add <package>`
- Remove a dependency: `uv remove <package>`
- Do not update `uv.lock` manually.

---

## Branching and Pull Requests

- **`main` is a protected branch. Never commit directly to `main`.**
- All changes must go through a feature branch and a pull request, regardless of how small.
- Branch naming convention: `<type>/<short-description>` (e.g. `fix/logging-cleanup`, `feat/new-capability`, `docs/update-readme`).
- Use conventional commit messages (see below) on every commit.
- Run `uv run pytest` and `uv run ruff check fritzexporter/` before pushing — CI will reject failures.

---

## CI / GitHub Actions

- **`run-tests.yaml`**: runs on PRs and pushes to `main`; executes linting (ruff check + ruff format) and pytest, uploads to SonarCloud.
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

- Check whether the base Python image version, build dependencies, or the install/entrypoint commands need updating to match changes in `pyproject.toml`, `uv.lock`, or the package itself.
- Verify that no unnecessary files are copied into the image and that the multi-stage build remains clean.
- The image installs the versions from `uv.lock` (`uv export` with hashes, then `pip install --require-hashes`); the uv build image tag pins the uv version.
- The image runs as `nobody` (65534), like the Helm chart; files it reads must be readable by that user.
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
