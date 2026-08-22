"""Project tasks for camas — the single definition shared by local dev and CI."""

from pathlib import Path

from camas import Claude, Config, Parallel, Sequential, Task

format = Task("ruff format {paths}", mutates=True, paths=".")

lint = Parallel(
    Task("ruff check {paths}", paths="."),
    Task("pydoclint {paths}", paths="src/smpclient"),
)

fix = Sequential(
    Task("ruff check --fix {paths}", mutates=True, paths="."),
    Task("ruff format {paths}", mutates=True, paths="."),
)

mypy = Task("mypy .")
pyright = Task("pyright")
typecheck = Parallel(mypy, pyright)

test = Task("pytest -v --ignore=tests/integration")

test_integration = Task(
    "pytest tests/integration -v --log-file=integration-tests.log "
    "--log-file-level=DEBUG --log-cli-level=INFO -o log_cli=true"
)

coverage = Task(
    "pytest --cov --cov-report=xml --cov-report=term-missing --ignore=tests/integration"
)

check = Parallel(lint, typecheck, test)

all = Sequential(format, check)

_PYTHONS = (Path(__file__).parent / ".python-version").read_text().split()

matrix = Parallel(
    Task("uv run --python {PY} camas check", env={"UV_PROJECT_ENVIRONMENT": ".venv-{PY}"}),
    matrix={"PY": tuple(_PYTHONS)},
)

_ = Config(
    default_task=all,
    github_task=check,
    agent=Claude(fix=fix, check=Parallel(lint, typecheck)),
)
