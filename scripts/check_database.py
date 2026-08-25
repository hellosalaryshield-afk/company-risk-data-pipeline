from sqlalchemy import text

from app.database.session import create_database_engine


def main() -> None:
    engine = create_database_engine()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    print("Database connection ok.")


if __name__ == "__main__":
    main()
