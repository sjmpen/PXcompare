"""`pxcompare-app [BASELINE CANDIDATE]`: start the Streamlit app on this machine only."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        sys.exit('The app needs Streamlit: pip install "pxcompare[app]"')
    app = Path(__file__).with_name("app.py")
    sys.argv = [
        "streamlit", "run", str(app),
        "--server.address", "localhost",
        "--browser.gatherUsageStats", "false",
        "--client.toolbarMode", "minimal",
        "--", *sys.argv[1:],
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
