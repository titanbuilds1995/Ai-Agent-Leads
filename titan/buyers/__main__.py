"""python -m titan.buyers area "<address>" [--radius 1.5] [--months 24]
   python -m titan.buyers build "<address>" [--price 269900]"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import area, build


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(prog="titan.buyers")
    ap.add_argument("step", choices=["area", "build"])
    ap.add_argument("address")
    ap.add_argument("--radius", type=float, default=1.5)
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--price", type=float, default=None, help="the deal's price, to flag buyers who buy in that range")
    ap.add_argument("--out", default="reports/buyers")
    a = ap.parse_args()
    if a.step == "area":
        p = area.pull(a.address, a.radius, a.months, a.out)
        print(f"\nWrote {p}\nNext: the Chrome step in docs/prompts/cash_buyers_llc.md, then `make buyers-build`.")
    else:
        folder = Path(a.out) / area.slug(a.address)
        s = build.build(folder, a.price)
        print(f"\n{s}\nLists in {folder}/: cash_buyers.csv, llc_people.csv, investor_agents.csv, summary.md")


if __name__ == "__main__":
    main()
