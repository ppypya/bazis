import os
import urllib.error
import urllib.request


def main() -> None:
    request = urllib.request.Request(
        "http://localhost:8000/api/v1/payments/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": os.environ["API_KEY"]},
    )

    try:
        urllib.request.urlopen(request, timeout=3)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise


if __name__ == "__main__":
    main()
