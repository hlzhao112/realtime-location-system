#!/usr/bin/env python3
"""按项目映射模拟台车沿工序前进上报。

用法:
  python scripts/simulate_report.py --token <ingest_token> --device LYG1 --epc "E2806894000040358004310A"
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--token", required=True)
    p.add_argument("--device", required=True)
    p.add_argument("--epc", required=True, help="一条或多条 EPC，逗号分隔")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--times", type=int, default=3)
    args = p.parse_args()
    epcs = [x.strip() for x in args.epc.split(",") if x.strip()]
    url = args.base.rstrip("/") + "/api/v1/ingest/report"
    for i in range(args.times):
        body = json.dumps({"deviceId": args.device, "epcs": epcs, "sportState": "moving"}).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "X-Ingest-Token": args.token},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            print(i + 1, resp.status, resp.read().decode())
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
