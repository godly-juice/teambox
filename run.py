"""Start the team box:  python3 run.py [--config /etc/teambox/config.json] [--port 8740]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from teambox.server import main  # noqa: E402

if __name__ == "__main__":
    main()
