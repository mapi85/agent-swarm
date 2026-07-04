"""Helpers de domaine partagés entre routers et runtime (évite les imports croisés)."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_ANCESTORS_SQL = text(
    """
    WITH RECURSIVE anc(id) AS (
        SELECT linked_task_id FROM task_links WHERE task_id = :task_id
        UNION
        SELECT tl.linked_task_id FROM task_links tl JOIN anc ON tl.task_id = anc.id
    )
    SELECT id FROM anc
    """
)


async def ancestor_ids(db: AsyncSession, task_id: int) -> set[int]:
    """Fermeture transitive des liens sortants : toute la chaîne d'ascendance
    (porosité). Utilisé par le runtime (accès aux artefacts) et l'API."""
    return {row[0] for row in (await db.execute(_ANCESTORS_SQL, {"task_id": task_id})).all()}
