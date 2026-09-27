Contributing
============

Thank you for contributing to fritz_exporter. This guide describes the local
development environment and the checks that changes must pass before a pull
request is opened.

Development environment
-----------------------

The project requires Python 3.14 or later and uses `uv <https://docs.astral.sh/uv/>`_
to manage its virtual environment and dependencies. Clone your fork, then install
all development dependency groups from the locked dependency set:

.. code-block:: bash

  git clone https://github.com/<your-account>/fritz_exporter.git
  cd fritz_exporter
  uv sync --all-groups

``uv`` creates and manages the project's virtual environment automatically.
Run project commands through ``uv run``; do not activate the environment or
install dependencies with ``pip`` manually.

Running locally
---------------

To run the exporter from the checked-out source, provide a configuration file
or the required environment variables:

.. code-block:: bash

  uv run python -m fritzexporter --config /path/to/config.yaml

See :doc:`configuration` for the available configuration options.

Verification
------------

Run all four checks before opening or updating a pull request:

.. code-block:: bash

  uv run ruff check .
  uv run ruff format --check .
  uv run ty check
  uv run pytest

These are the same lint, format, type-check, and test commands run by the
``Run Tests`` GitHub Actions workflow. ``pytest`` collects branch coverage and
writes ``coverage.xml`` as configured in ``pyproject.toml``.

Use ``uv run ruff format .`` to apply formatting fixes. Address Ruff findings
in production code rather than disabling rules unless a narrowly scoped
``noqa`` is necessary.

Managing dependencies
---------------------

Dependencies and tools are declared in ``pyproject.toml`` and locked in
``uv.lock``. Use ``uv`` to change them:

.. code-block:: bash

  uv add <package>
  uv remove <package>

Do not edit ``uv.lock`` by hand. Run ``uv sync --all-groups`` after pulling
dependency changes.

Pull requests
-------------

Create a topic branch from ``main``; do not commit directly to ``main``. Name
branches ``<type>/<short-description>``, such as ``fix/timeout-handling`` or
``docs/contributing-guide``.

Use Conventional Commit messages:

.. code-block:: text

  <type>(<optional scope>): <description>

The allowed types are ``feat``, ``fix``, ``perf``, ``refactor``, ``style``,
``test``, ``build``, ``ops``, ``docs``, and ``merge``. Pull requests should
include relevant tests and user-facing documentation changes.
