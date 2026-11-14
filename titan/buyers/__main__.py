"""python -m titan.buyers area "<address>" [--radius 1.5] [--months 24]
   python -m titan.buyers build "<address>" [--price 269900]"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import area, build


def status(root: Path) -> None:
    """What both buyer jobs have saved so far (the Tortuga LLC run and the Facebook groups run)."""
    import subprocess
    try:
        ps = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout.splitlines()
        running = sum(1 for l in ps if l.split(" ")[0].endswith("claude") or "/claude " in l)
    except Exception:  # noqa: BLE001
        running = -1
    print(f"Claude Code sessions open on this Mac: {running if running >= 0 else 'unknown'}")
    fb = area.read_csv(root / "fb_investors.csv")
    deals = area.read_csv(root / "fb_deals.csv")
    print(f"\nFACEBOOK GROUPS: {len(fb)} investor contacts, {len(deals)} deal posts saved"
          + ("" if fb or deals else "  (not started yet)"))
    for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        rows = area.read_csv(d / "area_sales.csv")
        owners = sum(1 for r in rows if r.get("buyer_name") or r.get("seller_name"))
        llcs, people = area.read_csv(d / "llcs.csv"), area.read_csv(d / "people.csv")
        print(f"\n{d.name.replace('_', ' ').upper()}: {len(rows)} houses pulled, owner found on {owners}, "
              f"{len(llcs)} LLCs looked up, {len(people)} people"
              + (", lists built (summary.md)" if (d / "summary.md").exists() else ""))
    if not root.exists():
        print("\nCASH BUYER RUN: not started yet (no reports/buyers folder)")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(prog="titan.buyers")
    ap.add_argument("step", choices=["area", "build", "status"])
    ap.add_argument("address")
    ap.add_argument("--radius", type=float, default=1.5)
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--price", type=float, default=None, help="the deal's price, to flag buyers who buy in that range")
    ap.add_argument("--out", default="reports/buyers")
    a = ap.parse_args()
    if a.step == "status":
        status(Path(a.out))
        return
    if a.step == "area":
        p = area.pull(a.address, a.radius, a.months, a.out)
        print(f"\nWrote {p}\nNext: the Chrome step in docs/prompts/cash_buyers_llc.md, then `make buyers-build`.")
    else:
        folder = Path(a.out) / area.slug(a.address)
        s = build.build(folder, a.price)
        print(f"\n{s}\nLists in {folder}/: cash_buyers.csv, llc_people.csv, investor_agents.csv, summary.md")


if __name__ == "__main__":
    main()
