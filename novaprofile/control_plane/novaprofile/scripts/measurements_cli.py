"""The command line for the measurement ledger.

Three verbs:

``list``
    Show every slug that has been measured, with the date and the
    conclusion of the newest reusable record.
``show``
    Print the newest reusable record of a slug -- the number to quote.
``run``
    Run a measurement and write it into the ledger automatically.

The point of ``run`` is that nobody has to remember to file a result.
A measurement that is not in the ledger is a measurement that will be
repeated.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from . import measurements
from .measurements import Measurement


def _slug_of(record: Measurement) -> str:
    return record.slug


def _cmd_list(args: argparse.Namespace) -> int:
    measurements.ensure_ledger_exists()
    grouped = measurements.all_records()
    if not grouped:
        print("the ledger is empty -- nothing has been measured yet")
        return 0

    print(f"{'slug':28s} {'date':17s} {'reusable':9s} conclusion")
    print("-" * 100)
    for slug in sorted(grouped):
        records = grouped[slug]
        current = next(
            (m for m in reversed(records) if m.reusable), records[-1]
        )
        flag = "yes" if current.reusable else "no"
        print(f"{_slug_of(current):28s} {current.measured_at[:16]:17s} {flag:9s} "
              f"{current.conclusion}")
    print()
    print(f"{len(measurements.load_all())} record(s) in "
          f"{measurements.DATA_DIR}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    record = measurements.latest(args.slug, reusable_only=not args.all)
    if record is None:
        print(f"no reusable measurement for '{args.slug}'")
        candidates = [m for m in measurements.load_all() if m.slug == args.slug]
        if candidates:
            print("there is a non-reusable record; pass --all to see it")
        return 1

    if args.json:
        print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
        return 0

    print(f"slug        {record.slug}")
    print(f"measured    {record.measured_at}")
    print(f"command     {record.command}")
    print(f"conclusion  {record.conclusion}")
    if record.notes:
        print(f"notes       {record.notes}")
    print()
    print("metrics:")
    for key, value in record.metrics.items():
        print(f"  {key:34s} {value}")
    print()
    env = record.environment
    print(f"environment {env.get('platform', '?')} | "
          f"python {env.get('python', '?')} | "
          f"{env.get('cpu_count', '?')} cpus")
    return 0


#: The measurements that ``run`` knows how to execute.
#:
#: Each entry is a small builder: it runs the measurement and returns the
#: metrics, the conclusion and whether the result may be reused.
_RUNNERS: dict[str, str] = {}


def _register(slug: str) -> None:
    """Mark a slug as runnable."""
    _RUNNERS[slug] = slug


def _run_scale_collision(args: argparse.Namespace) -> tuple[dict, str, bool]:
    """Run the collision scale check and file the result."""
    from .scale_check import run as run_scale

    report = run_scale(args.count, args.workers, args.digest_bits)
    collisions = int(report["total_collisions"])
    axes = int(report["axes_checked"])
    if collisions == 0:
        conclusion = (
            f"Zero collisions across all {axes} guarded axes "
            f"at {report['count']} profiles."
        )
    else:
        conclusion = (
            f"{collisions} collision(s) across {axes} guarded axes "
            f"at {report['count']} profiles."
        )
    return dict(report), conclusion, True


def _run_entropy_budget(args: argparse.Namespace) -> tuple[dict, str, bool]:
    """Record the entropy budget of every guarded axis."""
    from .entropy_budget import budget_report

    report = budget_report()
    worst = report.get("worst_axis")
    bits = report.get("worst_axis_bits", 0)
    conclusion = (
        f"Every identifying axis clears the {report['required_bits']:.1f}-bit "
        f"fleet threshold; the weakest is '{worst}' at {bits:.1f} bits."
    )
    return dict(report), conclusion, True


def _run_test_suite(args: argparse.Namespace) -> tuple[dict, str, bool]:
    """Run the test suite and lint, then file the result."""
    # The control plane -- the directory that holds the tests and the
    # linter configuration -- sits under the project root.
    root = measurements.PROJECT_ROOT / "control_plane"
    env = {"PYTHONPATH": str(root)}
    pytest = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**dict(__import__("os").environ), **env},
    )
    ruff = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "ruff", "check", "."],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**dict(__import__("os").environ), **env},
    )

    passed = failed = 0
    # The summary line looks like
    # ``545 passed, 2 skipped, 1 warning in 12.35s``; the counts sit at
    # the start of their comma-separated segment.
    for line in pytest.stdout.splitlines():
        if " passed" not in line and " failed" not in line:
            continue
        if not line.strip()[:1].isdigit():
            continue
        for token in line.split(","):
            token = token.strip()
            if token.endswith(" passed"):
                passed = int(token.split()[0])
            elif token.endswith(" failed"):
                failed = int(token.split()[0])

    metrics = {
        "pytest_passed": passed,
        "pytest_failed": failed,
        "ruff_clean": ruff.returncode == 0,
        "ruff_violations": ruff.stdout.count("error"),
    }
    if failed == 0 and ruff.returncode == 0:
        conclusion = f"{passed} tests pass and the linter is clean."
        reusable = True
    else:
        conclusion = f"{failed} test(s) failed; the linter is clean={ruff.returncode == 0}."
        reusable = False
    return metrics, conclusion, reusable


def _run_fleet_audit(args: argparse.Namespace) -> tuple[dict, str, bool]:
    """Run the four dimension audit and file the result.

    The audit is the measurement that answers the question the scale
    check cannot: not *whether* the profiles collide but whether they
    make sense.  A fleet can be perfectly collision free and still
    claim that a phone with an Exynos 2200 has an Adreno 730 inside it.
    """

    from .audit import run_audit

    report = run_audit(args.count)
    dimensions = report.dimensions
    errors = report.total_errors
    warnings = report.total_warnings
    passed = [name for name, d in dimensions.items() if d.passed]

    metrics: dict[str, object] = {
        "profiles_checked": args.count,
        "dimensions_passed": len(passed),
        "dimensions_total": len(dimensions),
        "errors": errors,
        "warnings": warnings,
    }
    for name, dimension in dimensions.items():
        metrics[f"{name}_passed"] = dimension.passed

    if errors == 0 and len(passed) == len(dimensions):
        conclusion = (
            f"All {len(dimensions)} audit dimensions pass across "
            f"{args.count} profiles with {warnings} warning(s)."
        )
        reusable = True
    else:
        conclusion = (
            f"{len(passed)}/{len(dimensions)} audit dimensions pass; "
            f"{errors} error(s)."
        )
        reusable = False
    return metrics, conclusion, reusable


def _cmd_run(args: argparse.Namespace) -> int:
    measurements.ensure_ledger_exists()

    if args.slug == "scale-collision":
        metrics, conclusion, reusable = _run_scale_collision(args)
        command = (
            f"python -m novaprofile.scripts.scale_check "
            f"--count {args.count} --workers {args.workers}"
        )
    elif args.slug == "entropy-budget":
        metrics, conclusion, reusable = _run_entropy_budget(args)
        command = "python -m novaprofile.scripts.entropy_budget"
    elif args.slug == "test-suite":
        metrics, conclusion, reusable = _run_test_suite(args)
        command = "python -m pytest -q && python -m ruff check ."
    elif args.slug == "fleet-audit":
        metrics, conclusion, reusable = _run_fleet_audit(args)
        command = f"python -m novaprofile.scripts.audit --count {args.count}"
    else:
        print(f"unknown measurement '{args.slug}'", file=sys.stderr)
        print(f"known: {', '.join(sorted(_RUNNERS))}", file=sys.stderr)
        return 2

    path = measurements.save(
        slug=args.slug,
        metrics=metrics,
        conclusion=conclusion,
        command=command,
        reusable=reusable,
    )
    print(f"recorded: {path}")
    print(conclusion)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    broken = measurements.verify()
    if broken:
        print(f"{len(broken)} broken record(s):")
        for item in broken:
            print(f"  {item}")
        return 1
    print(f"ledger ok: {len(measurements.load_all())} record(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m novaprofile.scripts.measurements",
        description="The measurement ledger",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="show every measured slug")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="print the newest record of a slug")
    p_show.add_argument("slug")
    p_show.add_argument("--all", action="store_true",
                        help="include records that are not reusable")
    p_show.add_argument("--json", action="store_true", help="print as JSON")
    p_show.set_defaults(func=_cmd_show)

    p_run = sub.add_parser("run", help="run a measurement and file it")
    p_run.add_argument(
        "slug",
        choices=["scale-collision", "entropy-budget", "test-suite", "fleet-audit"],
    )
    p_run.add_argument("--count", type=int, default=200_000)
    p_run.add_argument("--workers", type=int, default=8)
    p_run.add_argument("--digest-bits", type=int, default=64)
    p_run.set_defaults(func=_cmd_run)

    p_verify = sub.add_parser("verify", help="check every record's checksum")
    p_verify.set_defaults(func=_cmd_verify)

    return parser


def main() -> None:
    _register("scale-collision")
    _register("entropy-budget")
    _register("test-suite")
    _register("fleet-audit")

    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
