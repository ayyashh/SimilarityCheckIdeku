from typing import Any, Dict, List

from app.repositories.database import build_connection_string, safe_connection


class IdeaRepository:
    """Read idea metadata required by the submit-similarity API."""

    def __init__(self) -> None:
        self.connection_string = build_connection_string()

    def get_all(self) -> List[Dict[str, Any]]:
        sql = """
        SELECT
            Id,
            IdeaName,
            IdeaIssueBackground,
            IdeaSolution,
            ToDivisionId
        FROM dbo.Ideas
        ORDER BY Id
        """

        with safe_connection(self.connection_string) as conn:
            rows = conn.cursor().execute(sql).fetchall()

        return [
            {
                "IdeaId": row.Id,
                "IdeaName": row.IdeaName or "",
                "Background": row.IdeaIssueBackground or "",
                "Solution": row.IdeaSolution or "",
                "ToDivision": row.ToDivisionId or "",
            }
            for row in rows
        ]
