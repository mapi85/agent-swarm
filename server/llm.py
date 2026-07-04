"""Couche LLM — chantier 3 : listing des modèles d'un provider.

La boucle de complétion (sessions agentiques, superviseur de missions,
bascule de secours entre providers) arrive au chantier 4.
"""
import httpx

ANTHROPIC_DEFAULT_BASE = "https://api.anthropic.com"


async def fetch_models(ptype: str, base_url: str, api_key: str) -> list[str]:
    """Interroge l'endpoint /models du provider et renvoie les ids de modèles."""
    async with httpx.AsyncClient(timeout=20) as client:
        if ptype == "anthropic":
            base = (base_url or ANTHROPIC_DEFAULT_BASE).rstrip("/")
            response = await client.get(
                f"{base}/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
        else:  # openai-compatible : base_url inclut généralement /v1
            if not base_url:
                raise ValueError("base_url requis pour un provider OpenAI-compatible")
            response = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        response.raise_for_status()
        data = response.json().get("data", [])
        return [item["id"] for item in data if item.get("id")]
