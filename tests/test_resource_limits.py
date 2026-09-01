"""
Verify every container in the stack has CPU and memory limits defined in
.env.example and enforced via `deploy.resources.limits` in docker-compose.yml.

Exits non-zero (with details) if any service is missing CPU or memory limits,
or if a limit references an environment variable that isn't declared in
.env.example.
"""

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


def load_compose() -> dict:
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


def load_env_example_vars() -> set[str]:
    """Parse .env.example and return the set of declared variable names."""
    declared: set[str] = set()
    for raw in ENV_EXAMPLE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        # Strip optional `export ` prefix
        line = line.removeprefix("export ")
        key, _, _ = line.partition("=")
        declared.add(key.strip())
    return declared


def collect_services(compose: dict) -> dict[str, dict]:
    return compose.get("services", {}) or {}


def collect_x_data_mounts(compose: dict) -> set[str]:
    """Return any services that merge *x-data-mounts so we know not to
    confuse the anchors block with real services."""
    # The x-constants section is in the document root, not under `services`.
    # Return a set of keys at the top level that are NOT services.
    services_keys = set(collect_services(compose).keys())
    top_level = set((compose or {}).keys())
    return top_level - services_keys


def extract_var_name(value) -> str | None:
    """Given a compose env-style string like ``${FOO:-1}`` or ``${FOO}``,
    return the variable name (FOO) or None if it's a literal."""
    if not isinstance(value, str):
        return None
    if "${" not in value:
        return None
    inside = value.split("${", 1)[1].split("}", 1)[0]
    # Default-value syntax: NAME:-default or NAME-default
    name = inside.split(":", 1)[0].split("-", 1)[0] if ":" in inside or "-" in inside else inside
    # Pure literal fallback like ${:-default} has no name — treat as literal
    return name or None


def service_limits(service: dict) -> tuple[str | None, str | None, list[str]]:
    """Return (cpu, memory, referenced_env_vars) for a single service.

    Returns (None, None, []) if the service has no deploy.resources.limits.
    """
    deploy = service.get("deploy") or {}
    resources = deploy.get("resources") or {}
    limits = resources.get("limits") or {}
    cpu = limits.get("cpus")
    memory = limits.get("memory")
    referenced: list[str] = []
    for v in (cpu, memory):
        name = extract_var_name(v)
        if name:
            referenced.append(name)
    return cpu, memory, referenced


def main() -> int:
    compose = load_compose()
    services = collect_services(compose)
    declared = load_env_example_vars()

    # Sanity check: there must be services
    if not services:
        print("FAIL: docker-compose.yml defines no services", file=sys.stderr)
        return 1

    failures: list[str] = []
    services_checked = 0

    for name, cfg in services.items():
        services_checked += 1
        cpu, memory, referenced = service_limits(cfg)
        if cpu is None:
            failures.append(f"  - {name}: missing CPU limit (deploy.resources.limits.cpus)")
        if memory is None:
            failures.append(f"  - {name}: missing memory limit (deploy.resources.limits.memory)")
        for var in referenced:
            if var not in declared:
                failures.append(
                    f"  - {name}: limit references env var '{var}' which is not declared in .env.example"
                )

    print(f"Checked {services_checked} services: {', '.join(sorted(services))}")
    if failures:
        print("FAIL: resource limit violations:", file=sys.stderr)
        for line in failures:
            print(line, file=sys.stderr)
        return 1
    print("OK: every service has CPU and memory limits, and every limit's env var is declared in .env.example")
    return 0


if __name__ == "__main__":
    sys.exit(main())
