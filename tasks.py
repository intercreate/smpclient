"""Project tasks for camas — the single definition shared by local dev and CI."""

from pathlib import Path

from camas import Config, Parallel, Sequential, Task

format = Task("ruff format .", mutates=True)

lint = Sequential(
    Task("ruff check ."),
    Task("pydoclint src/smpclient"),
)

fix = Sequential(
    Task("ruff check --fix .", mutates=True),
    Task("ruff format .", mutates=True),
)

typecheck = Task("mypy .")

test = Task("pytest --maxfail=1 --ignore=tests/integration")

test_integration = Task(
    "pytest tests/integration --log-file=integration-tests.log "
    "--log-file-level=DEBUG --log-cli-level=INFO -o log_cli=true"
)

coverage = Task(
    "pytest --cov --cov-report=xml --cov-report=term-missing --ignore=tests/integration"
)

check = Parallel(lint, typecheck, test)

all = Sequential(format, check)

_PYTHONS = (Path(__file__).parent / ".python-version").read_text().split()

matrix = Sequential(
    *(
        Task(
            f"uv run --python {v} camas all",
            name=f"all-{v}",
            env={"UV_PROJECT_ENVIRONMENT": f".venv-{v}"},
        )
        for v in _PYTHONS
    )
)

_ = Config(default_task=all, github_task=check)
