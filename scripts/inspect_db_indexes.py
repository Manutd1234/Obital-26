from __future__ import annotations

import os
from pathlib import Path

import sqlalchemy as sa


def load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_dotenv()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("NO_DATABASE_URL")
        return

    engine = sa.create_engine(
        url,
        future=True,
        poolclass=sa.pool.NullPool,
        connect_args={"prepare_threshold": None},
    )

    plans = [
        (
            "list_docs_meta",
            """
            EXPLAIN
            SELECT account_id, relative_path, sha256, size_bytes, updated_at,
                   deleted_at, expires_at, hydration_status
            FROM account_artifacts
            WHERE account_id = 'admin'
              AND (relative_path LIKE 'documents/%'
                   OR relative_path LIKE 'output/raw_text/%')
              AND deleted_at IS NULL
            """,
        ),
        (
            "hydrate_candidates",
            """
            EXPLAIN
            SELECT *
            FROM account_artifacts
            WHERE account_id = 'admin'
              AND hydration_status != 'full'
            ORDER BY size_bytes ASC, updated_at DESC
            LIMIT 20
            """,
        ),
        (
            "chat_sessions_latest",
            """
            EXPLAIN
            SELECT *
            FROM chat_sessions
            WHERE account_id = 'admin'
            ORDER BY updated_at DESC
            """,
        ),
        (
            "reward_sessions_latest",
            """
            EXPLAIN
            SELECT *
            FROM reward_sessions
            WHERE account_id = 'admin'
            ORDER BY created_at DESC
            LIMIT 20
            """,
        ),
        (
            "bin_latest",
            """
            EXPLAIN
            SELECT *
            FROM bin_items
            WHERE account_id = 'admin'
            ORDER BY deleted_at DESC
            """,
        ),
        (
            "unlock_active",
            """
            EXPLAIN
            SELECT *
            FROM account_unlock_sessions
            WHERE account_id = 'admin'
              AND expires_at > now()
            ORDER BY created_at DESC
            LIMIT 1
            """,
        ),
    ]

    with engine.connect() as conn:
        print("connected", conn.exec_driver_sql("select current_database()").scalar_one())
        print("tables")
        for row in conn.exec_driver_sql(
            "select relname, n_live_tup from pg_stat_user_tables order by relname"
        ):
            print(row.relname, row.n_live_tup)

        print("indexes")
        for row in conn.exec_driver_sql(
            """
            select tablename, indexname, indexdef
            from pg_indexes
            where schemaname = 'public'
            order by tablename, indexname
            """
        ):
            print(row.tablename, row.indexname, row.indexdef)

        print("plans")
        for name, sql in plans:
            print(name)
            for row in conn.execute(sa.text(sql)):
                print(" ", row[0])


if __name__ == "__main__":
    main()
