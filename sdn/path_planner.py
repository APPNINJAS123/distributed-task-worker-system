import json
import time

from common.config import LOADS_FILE

PATHS = ["s2", "s3"]
STALE_AFTER = 10


def read_worker_loads(path=LOADS_FILE):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if time.time() - data.get("updated", 0) > STALE_AFTER:
        return {}
    return data.get("workers", {})


def plan_paths(loads, paths=PATHS):
    totals = {p: 0.0 for p in paths}
    plan = {}
    for key, load in sorted(loads.items(), key=lambda item: (-item[1], item[0])):
        best = min(paths, key=lambda p: (totals[p], PATHS.index(p)))
        plan[key] = best
        totals[best] += load + 1
    return plan, totals


def main():
    print(f"watching {LOADS_FILE} (ctrl+c to stop)", flush=True)
    last = None
    try:
        while True:
            workers = read_worker_loads()
            loads = {wid: float(info["load"]) for wid, info in workers.items()}
            plan, totals = plan_paths(loads)
            if plan != last:
                stamp = time.strftime("%H:%M:%S")
                if not loads:
                    print(f"{stamp} no workers reported yet", flush=True)
                else:
                    print(f"{stamp} loads: " + " ".join(f"{w}={l:.0f}%" for w, l in sorted(loads.items())), flush=True)
                    for path in PATHS:
                        members = sorted(w for w, p in plan.items() if p == path)
                        print(f"         path via {path}: {', '.join(members) or '-'}  (total load {totals[path]:.0f})", flush=True)
                last = plan
            time.sleep(3)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
