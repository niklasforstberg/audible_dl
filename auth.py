"""One-time login via browser. Creates auth.json so download.py can reuse the session."""
import readline  # noqa: F401 -- removes macOS terminal's ~1024-char limit when pasting a URL
import audible

AUTH_FILE = "auth.json"


def main():
    print("Log in to your Audible account via the browser.")
    marketplace = input("Marketplace (e.g. us, de, uk) [us]: ").strip() or "us"

    auth = audible.Authenticator.from_login_external(locale=marketplace)
    auth.to_file(AUTH_FILE)
    print(f"Saved session to {AUTH_FILE}")


if __name__ == "__main__":
    main()
