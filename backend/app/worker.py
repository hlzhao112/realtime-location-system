"""独立消费者进程：python -m app.worker"""

from .api.ingest import process_queued
from .services.queue import consume_one


def main():
    print("ingest worker started")
    while True:
        consume_one(process_queued)


if __name__ == "__main__":
    main()
