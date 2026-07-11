"""One-time login. Creates auth.json so download.py can reuse the session."""
import audible

AUTH_FILE = "auth.json"


def main():
    print("Logga in på ditt Audible-konto.")
    marketplace = input("Marknadsplats (t.ex. de, us, uk, se) [de]: ").strip() or "de"
    username = input("E-post: ").strip()
    password = input("Lösenord: ").strip()

    auth = audible.Authenticator.from_login(
        username,
        password,
        locale=marketplace,
        with_username=False,
    )
    auth.to_file(AUTH_FILE)
    print(f"Sparade session i {AUTH_FILE}")


if __name__ == "__main__":
    main()
