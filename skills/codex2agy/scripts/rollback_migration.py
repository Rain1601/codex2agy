#!/usr/bin/env python3
"""Rollback / Undo CLI for codex2agy migrations (zero-dependency, cross-platform).

Usage:
  python3 rollback_migration.py --list
  python3 rollback_migration.py --latest [--dry-run]
  python3 rollback_migration.py --run-id <RUN_ID> [--dry-run]
"""

import argparse
import json
import sys

from ledger import JsonLedger, list_runs, migration_lock, rollback_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or undo codex2agy migration runs")
    parser.add_argument("--ledger-dir", default=None, help="Override ledger directory (for testing)")
    parser.add_argument("--list", action="store_true", help="List recorded migration runs")
    parser.add_argument("--latest", action="store_true", help="Rollback the most recent active/committed run")
    parser.add_argument("--run-id", default=None, help="Rollback a specific run_id")
    parser.add_argument("--dry-run", action="store_true", help="Preview rollback actions without deleting")
    args = parser.parse_args()

    if args.list:
        print(json.dumps({"runs": list_runs(args.ledger_dir)}, ensure_ascii=False, indent=2))
        return 0

    if not args.latest and not args.run_id:
        parser.error("Specify one of --list, --latest, or --run-id <RUN_ID>")

    with migration_lock(args.ledger_dir):
        ledger = JsonLedger(args.ledger_dir, readonly=args.dry_run)
        target_id = args.run_id
        if args.latest:
            candidates = [
                r for r in (ledger.data.get("runs") or {}).values()
                if r.get("status") in ("committed", "running")
            ]
            candidates.sort(key=lambda x: x.get("created_at") or "", reverse=True)
            if not candidates:
                print(json.dumps({"message": "No committed or running migration found to roll back."}, ensure_ascii=False, indent=2))
                return 0
            target_id = candidates[0]["run_id"]

        report = rollback_run(
            ledger,
            target_id,
            final_status="rolled_back",
            reason="manual_rollback",
            dry_run=args.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("error") or report.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
