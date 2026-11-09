"""
Protection layer tests — MUST ALL PASS before any other module is built.

Covers:
  - Layer 4: SQL guard (_execute allows SELECT/SHOW/SET SESSION, refuses all DML)
  - Layer 2: Grant verification (_verify_grants refuses root/kodi/ALL PRIVILEGES/*.*)
  - Layer 5: Static analysis (no write SQL anywhere under kodi/)
  - Layer 6: JSON-RPC allowlist (blocks write methods, allows read methods)
"""

import ast
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from logsaga.kodi.db import (
    KodiDBSecurityError,
    _execute,
    _verify_grants,
)
from logsaga.kodi.jsonrpc import (
    KodiJSONRPCSecurityError,
    _check_method,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_dict_cursor(rows=None):
    """Return a MagicMock that behaves like a pymysql DictCursor."""
    cur = MagicMock()
    cur.fetchone.return_value = rows[0] if rows else None
    cur.fetchall.return_value = rows or []
    return cur


def _make_conn_with_grants(user: str, grants: list[str]) -> MagicMock:
    """
    Return a mock pymysql connection where:
      - SELECT USER() returns `user`
      - SHOW GRANTS FOR CURRENT_USER() returns `grants`
    """
    cur = MagicMock()

    def execute_side_effect(sql, *args, **kwargs):
        pass

    def fetchone_side_effect():
        # Called once for SELECT USER()
        return (user,)

    def fetchall_side_effect():
        # Called once for SHOW GRANTS
        return [(g,) for g in grants]

    cur.execute.side_effect = execute_side_effect
    cur.fetchone.side_effect = fetchone_side_effect
    cur.fetchall.side_effect = fetchall_side_effect
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


# ---------------------------------------------------------------------------
# Layer 4: SQL guard — allowed statements
# ---------------------------------------------------------------------------
class TestSQLGuardAllows:
    def test_select(self):
        cur = _make_dict_cursor()
        _execute(cur, "SELECT idFile FROM files WHERE idFile = %s", (1,))
        cur.execute.assert_called_once()

    def test_select_case_insensitive(self):
        cur = _make_dict_cursor()
        _execute(cur, "select * from files")
        cur.execute.assert_called_once()

    def test_show_databases(self):
        cur = _make_dict_cursor()
        _execute(cur, "SHOW DATABASES LIKE 'MyVideos%'")
        cur.execute.assert_called_once()

    def test_show_grants(self):
        cur = _make_dict_cursor()
        _execute(cur, "SHOW GRANTS FOR CURRENT_USER()")
        cur.execute.assert_called_once()

    def test_set_session_isolation(self):
        cur = _make_dict_cursor()
        _execute(cur, "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cur.execute.assert_called_once()

    def test_set_session_read_only(self):
        cur = _make_dict_cursor()
        _execute(cur, "SET SESSION TRANSACTION READ ONLY")
        cur.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Layer 4: SQL guard — rejected statements
# ---------------------------------------------------------------------------
class TestSQLGuardRejects:
    def test_insert(self):
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError, match="INSERT"):
            _execute(cur, "INSERT INTO files VALUES (1, 2, 'x', 1, '2026-01-01', '2026-01-01')")
        cur.execute.assert_not_called()

    def test_update(self):
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError, match="UPDATE"):
            _execute(cur, "UPDATE files SET playCount = 0 WHERE idFile = 1")
        cur.execute.assert_not_called()

    def test_delete(self):
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError, match="DELETE"):
            _execute(cur, "DELETE FROM bookmark WHERE idBookmark = 1")
        cur.execute.assert_not_called()

    @pytest.mark.parametrize("verb", ["DROP", "ALTER", "CREATE", "TRUNCATE", "REPLACE"])
    def test_ddl_and_dml(self, verb: str):
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError):
            _execute(cur, f"{verb} TABLE files")
        cur.execute.assert_not_called()

    def test_grant(self):
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError):
            _execute(cur, "GRANT SELECT ON *.* TO 'x'@'%'")
        cur.execute.assert_not_called()

    def test_lock(self):
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError):
            _execute(cur, "LOCK TABLES files READ")
        cur.execute.assert_not_called()

    def test_call(self):
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError):
            _execute(cur, "CALL some_procedure()")
        cur.execute.assert_not_called()

    def test_set_global_rejected(self):
        """SET GLOBAL is not allowed; only SET SESSION."""
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError, match="SET SESSION"):
            _execute(cur, "SET GLOBAL max_connections = 100")
        cur.execute.assert_not_called()

    def test_bare_set_rejected(self):
        """Bare SET @var is not allowed."""
        cur = _make_dict_cursor()
        with pytest.raises(KodiDBSecurityError, match="SET SESSION"):
            _execute(cur, "SET @foo = 1")
        cur.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Layer 2: Grant verification
# ---------------------------------------------------------------------------
class TestGrantVerification:
    def test_accepts_logsaga_user(self):
        """A SELECT-only grant on MyVideos%.* must be accepted without error."""
        conn = _make_conn_with_grants(
            user="logsaga@192.168.1.1",
            grants=[
                "GRANT USAGE ON *.* TO `logsaga`@`%`",
                "GRANT SELECT ON `MyVideos%`.* TO `logsaga`@`%`",
            ],
        )
        _verify_grants(conn)  # must not raise

    def test_rejects_root_user(self):
        conn = _make_conn_with_grants(
            user="root@localhost",
            grants=["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION"],
        )
        with pytest.raises(KodiDBSecurityError, match="root"):
            _verify_grants(conn)

    def test_rejects_kodi_user(self):
        conn = _make_conn_with_grants(
            user="kodi@%",
            grants=["GRANT ALL PRIVILEGES ON *.* TO `kodi`@`%`"],
        )
        with pytest.raises(KodiDBSecurityError, match="kodi"):
            _verify_grants(conn)

    def test_rejects_all_privileges(self):
        conn = _make_conn_with_grants(
            user="someuser@%",
            grants=["GRANT ALL PRIVILEGES ON `MyVideos%`.* TO `someuser`@`%`"],
        )
        with pytest.raises(KodiDBSecurityError, match="ALL PRIVILEGES"):
            _verify_grants(conn)

    def test_rejects_star_star_grant(self):
        """Any grant on *.* is too broad, even if it's just USAGE + SELECT."""
        conn = _make_conn_with_grants(
            user="someuser@%",
            grants=[
                "GRANT USAGE ON *.* TO `someuser`@`%`",
                "GRANT SELECT ON *.* TO `someuser`@`%`",  # *.* = too broad
            ],
        )
        with pytest.raises(KodiDBSecurityError, match=r"\*\.\*"):
            _verify_grants(conn)

    def test_rejects_insert_grant(self):
        conn = _make_conn_with_grants(
            user="baduser@%",
            grants=[
                "GRANT USAGE ON *.* TO `baduser`@`%`",
                "GRANT SELECT, INSERT ON `MyVideos%`.* TO `baduser`@`%`",
            ],
        )
        with pytest.raises(KodiDBSecurityError, match="INSERT"):
            _verify_grants(conn)

    def test_rejects_update_grant(self):
        conn = _make_conn_with_grants(
            user="baduser@%",
            grants=[
                "GRANT USAGE ON *.* TO `baduser`@`%`",
                "GRANT SELECT, UPDATE ON `MyVideos%`.* TO `baduser`@`%`",
            ],
        )
        with pytest.raises(KodiDBSecurityError, match="UPDATE"):
            _verify_grants(conn)


# ---------------------------------------------------------------------------
# Layer 5: Static analysis — no write SQL in kodi/ module
# ---------------------------------------------------------------------------
_KODI_MODULE_DIR = Path(__file__).parent.parent / "logsaga" / "kodi"

# Match actual SQL DML/DDL patterns — not standalone keywords or English prose.
# Each pattern requires the verb followed by its characteristic SQL context word,
# so error messages and docstrings that mention these keywords are not flagged.
_FORBIDDEN_SQL_PATTERNS = re.compile(
    r"\b(?:"
    r"INSERT\s+(?:INTO|IGNORE|LOW_PRIORITY)|"
    r"UPDATE\s+\w+\s+SET\s|"
    r"DELETE\s+FROM\s|"
    r"DROP\s+(?:TABLE|DATABASE|INDEX|VIEW)\b|"
    r"ALTER\s+(?:TABLE|DATABASE|SCHEMA|VIEW)\b|"
    r"CREATE\s+(?:TABLE|DATABASE|INDEX|VIEW|SCHEMA)\b|"
    r"TRUNCATE\s+(?:TABLE\s+)?\w+\s|"
    r"REPLACE\s+INTO\b|"
    r"LOCK\s+TABLES\s|"
    r"CALL\s+\w+\s*\("
    r")",
    re.IGNORECASE,
)


class TestNoWriteSQLInKodiModule:
    def _get_string_literals(self, source: str) -> list[str]:
        """Extract all string literals from Python source using AST."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        strings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value)
        return strings

    def test_no_write_sql_in_kodi_module(self):
        """
        Scan every .py file under logsaga/kodi/ and assert that no string
        literal contains forbidden SQL keywords.

        This is a build-breaker: if someone accidentally adds an UPDATE or DELETE
        inside a SQL constant, this test catches it immediately.
        """
        violations: list[str] = []

        for py_file in _KODI_MODULE_DIR.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            for literal in self._get_string_literals(source):
                match = _FORBIDDEN_SQL_PATTERNS.search(literal)
                if match:
                    violations.append(
                        f"{py_file.name}: string {literal[:80]!r} "
                        f"contains forbidden keyword '{match.group()}'"
                    )

        assert not violations, (
            "Write SQL found in kodi/ module:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Layer 6: JSON-RPC allowlist
# ---------------------------------------------------------------------------
class TestJSONRPCAllowlist:
    def test_allows_ping(self):
        _check_method("JSONRPC.Ping")  # must not raise

    def test_allows_prepare_download(self):
        _check_method("Files.PrepareDownload")  # must not raise

    def test_allows_get_movies(self):
        _check_method("VideoLibrary.GetMovies")  # must not raise

    def test_allows_get_episodes(self):
        _check_method("VideoLibrary.GetEpisodes")  # must not raise

    def test_allows_get_movie_details(self):
        _check_method("VideoLibrary.GetMovieDetails")  # must not raise

    def test_blocks_set_episode_details(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("VideoLibrary.SetEpisodeDetails")

    def test_blocks_set_movie_details(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("VideoLibrary.SetMovieDetails")

    def test_blocks_remove_movie(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("VideoLibrary.RemoveMovie")

    def test_blocks_clean(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("VideoLibrary.Clean")

    def test_blocks_scan(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("VideoLibrary.Scan")

    def test_blocks_player_open(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("Player.Open")

    def test_blocks_input_select(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("Input.Select")

    def test_blocks_system_reboot(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("System.Reboot")

    def test_blocks_addons_execute(self):
        with pytest.raises(KodiJSONRPCSecurityError):
            _check_method("Addons.ExecuteAddon")
