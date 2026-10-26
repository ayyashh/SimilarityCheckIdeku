from __future__ import annotations
import argparse
from app.auth.jwt_service import create_token

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an API bearer token.")
    parser.add_argument(
        "--subject",
        default="similarity-api-client",
        help="Token subject/client name (default: similarity-api-client).",
    )
    args = parser.parse_args()

    print(create_token(subject=args.subject))

if __name__ == "__main__":
    main()
