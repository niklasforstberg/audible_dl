"""One-time login via webbläsare. Skapar auth.json så download.py kan återanvända sessionen."""
import readline  # noqa: F401 -- tar bort macOS-terminalens ~1024-teckens gräns vid inklistring av URL
import audible

AUTH_FILE = "auth.json"


def main():
    print("Logga in på ditt Audible-konto via webbläsaren.")
    marketplace = input("Marknadsplats (t.ex. us, de, uk) [us]: ").strip() or "us"

    auth = audible.Authenticator.from_login_external(locale=marketplace)
    auth.to_file(AUTH_FILE)
    print(f"Sparade session i {AUTH_FILE}")


if __name__ == "__main__":
    main()
