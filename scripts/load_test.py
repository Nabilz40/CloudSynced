import concurrent.futures
import time
import urllib.request

URL = "http://127.0.0.1:8080/api/stress?seconds=5"
DURATION_SECONDS = 45
WORKERS = 6


def hit_stress(worker_id: int, end_time: float) -> int:
    count = 0
    while time.time() < end_time:
        try:
            with urllib.request.urlopen(URL, timeout=15) as resp:
                resp.read()
                count += 1
        except Exception as exc:
            print(f"[worker {worker_id}] error: {exc}")
            time.sleep(0.5)
    return count


if __name__ == "__main__":
    print(f"Sending concurrent CPU stress traffic to {URL} for {DURATION_SECONDS}s...")
    end_time = time.time() + DURATION_SECONDS
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda wid: hit_stress(wid, end_time), range(WORKERS)))
    print(f"Done! Completed {sum(results)} stress requests across {WORKERS} workers.")
