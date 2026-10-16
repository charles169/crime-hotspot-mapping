import getpass
import re
from pathlib import Path


ENV_PATH = Path(__file__).resolve().parent / ".env"
KEY_NAMES = (
    "CRIME_RADAR_TURNSTILE_SITE_KEY",
    "CRIME_RADAR_TURNSTILE_SECRET_KEY",
)
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def main():
    site_key = input("Paste the Turnstile Site key: ").strip()
    secret_key = getpass.getpass("Paste the Turnstile Secret key: ").strip()

    if not KEY_PATTERN.fullmatch(site_key):
        raise SystemExit("Site key format is invalid; .env was not changed.")
    if not KEY_PATTERN.fullmatch(secret_key):
        raise SystemExit("Secret key format is invalid; .env was not changed.")

    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    retained_lines = [
        line
        for line in lines
        if not any(
            re.match(rf"^\s*{re.escape(key)}\s*=", line)
            for key in KEY_NAMES
        )
    ]
    retained_lines.extend(
        (
            f"{KEY_NAMES[0]}={site_key}",
            f"{KEY_NAMES[1]}={secret_key}",
        )
    )
    ENV_PATH.write_text(
        "\n".join(retained_lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("Turnstile keys saved locally. Secret value was not displayed.")


if __name__ == "__main__":
    main()
