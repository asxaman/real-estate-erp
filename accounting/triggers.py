"""Database-level locks for the ledger (SQLite).

The Python code already refuses to change posted entries. These triggers are a
second lock inside the database file itself, so even a script, a shell session
or a bug cannot quietly rewrite the books.
"""


STATEMENTS = [
    # A posted entry can never be changed or deleted.
    """
    CREATE TRIGGER IF NOT EXISTS ledger_entry_no_update
    BEFORE UPDATE ON accounting_journalentry
    WHEN OLD.status = 'posted'
    BEGIN
        SELECT RAISE(ABORT, 'Posted journal entries cannot be changed.');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS ledger_entry_no_delete
    BEFORE DELETE ON accounting_journalentry
    WHEN OLD.status = 'posted'
    BEGIN
        SELECT RAISE(ABORT, 'Posted journal entries cannot be deleted.');
    END
    """,
    # An entry can only become "posted" if it has two or more lines and
    # total debits equal total credits.
    """
    CREATE TRIGGER IF NOT EXISTS ledger_entry_must_balance
    BEFORE UPDATE ON accounting_journalentry
    WHEN OLD.status = 'draft' AND NEW.status = 'posted'
    BEGIN
        SELECT RAISE(ABORT, 'A journal entry needs at least two lines.')
        WHERE (SELECT COUNT(*) FROM accounting_journalline WHERE entry_id = OLD.id) < 2;
        SELECT RAISE(ABORT, 'Debits must equal credits.')
        WHERE ROUND((SELECT COALESCE(SUM(CAST(debit AS REAL)), 0) FROM accounting_journalline WHERE entry_id = OLD.id), 2)
           <> ROUND((SELECT COALESCE(SUM(CAST(credit AS REAL)), 0) FROM accounting_journalline WHERE entry_id = OLD.id), 2);
    END
    """,
    # Lines of a posted entry can never be added, changed or deleted.
    """
    CREATE TRIGGER IF NOT EXISTS ledger_line_no_insert
    BEFORE INSERT ON accounting_journalline
    WHEN (SELECT status FROM accounting_journalentry WHERE id = NEW.entry_id) = 'posted'
    BEGIN
        SELECT RAISE(ABORT, 'Lines cannot be added to a posted journal entry.');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS ledger_line_no_update
    BEFORE UPDATE ON accounting_journalline
    WHEN (SELECT status FROM accounting_journalentry WHERE id = OLD.entry_id) = 'posted'
    BEGIN
        SELECT RAISE(ABORT, 'Lines of a posted journal entry cannot be changed.');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS ledger_line_no_delete
    BEFORE DELETE ON accounting_journalline
    WHEN (SELECT status FROM accounting_journalentry WHERE id = OLD.entry_id) = 'posted'
    BEGIN
        SELECT RAISE(ABORT, 'Lines of a posted journal entry cannot be deleted.');
    END
    """,
    # The audit trail can never be rewritten.
    """
    CREATE TRIGGER IF NOT EXISTS ledger_audit_no_update
    BEFORE UPDATE ON accounting_auditlog
    BEGIN
        SELECT RAISE(ABORT, 'The audit log cannot be changed.');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS ledger_audit_no_delete
    BEFORE DELETE ON accounting_auditlog
    BEGIN
        SELECT RAISE(ABORT, 'The audit log cannot be deleted.');
    END
    """,
]


def install_triggers(sender=None, **kwargs):
    using = kwargs.get("using", "default")
    from django.db import connections

    conn = connections[using]
    if conn.vendor != "sqlite":
        return
    tables = conn.introspection.table_names()
    if "accounting_journalentry" not in tables or "accounting_auditlog" not in tables:
        return
    with conn.cursor() as cursor:
        for sql in STATEMENTS:
            cursor.execute(sql)


def drop_triggers(sender=None, **kwargs):
    """Lift the ledger locks while `migrate` runs. SQLite cannot rebuild a table
    that a trigger refers to. They are installed again right after (post_migrate)."""
    using = kwargs.get("using", "default")
    from django.db import connections

    conn = connections[using]
    if conn.vendor != "sqlite":
        return
    with conn.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'ledger_%'")
        for (name,) in cursor.fetchall():
            cursor.execute(f'DROP TRIGGER IF EXISTS "{name}"')
