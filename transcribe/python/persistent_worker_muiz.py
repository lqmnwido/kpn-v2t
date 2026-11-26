"""Compatibility entry point: both configurations use the same meeting pipeline."""
from persistent_worker import process_task
import json
import sys

if __name__ == "__main__":
    print(json.dumps({"status": "ready"}), flush=True)
    for line in sys.stdin:
        try:
            response = process_task(json.loads(line))
        except Exception as error:
            response = {"status": "error", "message": str(error)}
        print(json.dumps(response, allow_nan=False), flush=True)
