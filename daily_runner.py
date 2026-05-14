import signal
import sys

from app import main


def _handle_signal(sig, frame):
    print("\nInterrupted — cleaning up...")
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

if __name__ == "__main__":
    main()
