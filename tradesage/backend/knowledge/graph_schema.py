"""
Neo4j graph schema: constraints, indexes, and vector indexes.
Run once at startup to ensure idempotent schema setup.
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import ServiceUnavailable, AuthError

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Node Labels
# ──────────────────────────────────────────────
NODE_LABELS = [
    "Company",
    "Sector",
    "AssetClass",
    "NewsEvent",
    "MacroEvent",
    "PriceMovement",
    "MarketPattern",
    "TraderPrinciple",
    "Trade",
    "Lesson",
    "BookChunk",
    "WebArticle",
    "Mentor",
]

# ──────────────────────────────────────────────
# Uniqueness Constraints
# ──────────────────────────────────────────────
CONSTRAINTS = [
    ("Company", "ticker"),
    ("Sector", "name"),
    ("AssetClass", "name"),
    ("MarketPattern", "name"),
    ("TraderPrinciple", "principle_name"),
    ("Mentor", "name"),
    ("Trade", "trade_id"),
    ("Lesson", "lesson_id"),
    ("BookChunk", "chunk_id"),
    ("WebArticle", "url"),
    ("NewsEvent", "event_id"),
    ("MacroEvent", "macro_id"),
]

# ──────────────────────────────────────────────
# Regular Indexes
# ──────────────────────────────────────────────
INDEXES = [
    ("NewsEvent", "ticker"),
    ("NewsEvent", "timestamp"),
    ("NewsEvent", "sentiment_score"),
    ("PriceMovement", "ticker"),
    ("PriceMovement", "timestamp"),
    ("Trade", "ticker"),
    ("Trade", "outcome"),
    ("Lesson", "timestamp"),
    ("MacroEvent", "type"),
    ("MacroEvent", "date"),
]

# ──────────────────────────────────────────────
# Vector Index Definitions
# ──────────────────────────────────────────────
VECTOR_INDEXES = [
    {
        "name": "bookchunk_embedding",
        "label": "BookChunk",
        "property": "embedding",
        "dimensions": 1536,
        "similarity": "cosine",
    },
    {
        "name": "webarticle_embedding",
        "label": "WebArticle",
        "property": "embedding",
        "dimensions": 1536,
        "similarity": "cosine",
    },
]


def wait_for_neo4j(uri: str, user: str, password: str, timeout: int = 10) -> Optional[Driver]:
    """Try to connect to Neo4j; return None if unavailable (non-blocking)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            driver = GraphDatabase.driver(uri, auth=(user, password))
            driver.verify_connectivity()
            logger.info("Connected to Neo4j at %s", uri)
            return driver
        except (ServiceUnavailable, AuthError, Exception) as exc:
            logger.debug("Neo4j not ready yet (%s), retrying...", exc)
            time.sleep(2)
    logger.warning(
        "Neo4j unavailable at %s — starting in degraded mode (graph features disabled). "
        "Install Docker and run: docker run -d --name tradesage-neo4j "
        "-p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/tradesage neo4j:5-community",
        uri,
    )
    return None


def ensure_neo4j_running() -> None:
    """Start the Neo4j Docker container if it isn't running."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=tradesage-neo4j", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        if "tradesage-neo4j" in result.stdout:
            logger.info("Neo4j Docker container is already running.")
            return

        logger.info("Starting Neo4j Docker container...")
        subprocess.run(
            [
                "docker", "run", "-d",
                "--name", "tradesage-neo4j",
                "--restart", "unless-stopped",
                "-p", "7474:7474",
                "-p", "7687:7687",
                "-e", "NEO4J_AUTH=neo4j/tradesage",
                "-e", "NEO4J_PLUGINS=[\"apoc\"]",
                "-e", "NEO4J_apoc_export_file_enabled=true",
                "-e", "NEO4J_apoc_import_file_enabled=true",
                "-v", "tradesage_neo4j_data:/data",
                "neo4j:5-community",
            ],
            check=True,
            timeout=30,
        )
        logger.info("Neo4j Docker container started. Waiting for it to be ready...")
        time.sleep(15)  # give Neo4j time to initialise
    except FileNotFoundError:
        logger.warning("Docker not found. Assuming Neo4j is already running externally.")
    except subprocess.CalledProcessError as exc:
        if "already in use" in str(exc.stderr or ""):
            logger.info("Neo4j container already exists — starting it.")
            subprocess.run(["docker", "start", "tradesage-neo4j"], timeout=15)
            time.sleep(10)
        else:
            logger.error("Failed to start Neo4j container: %s", exc)


def apply_schema(driver: Driver) -> None:
    """Create all constraints, indexes, and vector indexes idempotently."""
    with driver.session() as session:
        # Constraints
        for label, prop in CONSTRAINTS:
            cname = f"unique_{label.lower()}_{prop}"
            try:
                session.run(
                    f"CREATE CONSTRAINT {cname} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
                )
                logger.debug("Constraint %s ensured.", cname)
            except Exception as exc:
                logger.warning("Constraint %s: %s", cname, exc)

        # Regular indexes
        for label, prop in INDEXES:
            iname = f"idx_{label.lower()}_{prop}"
            try:
                session.run(
                    f"CREATE INDEX {iname} IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.{prop})"
                )
                logger.debug("Index %s ensured.", iname)
            except Exception as exc:
                logger.warning("Index %s: %s", iname, exc)

        # Vector indexes (Neo4j 5.11+)
        for vi in VECTOR_INDEXES:
            try:
                session.run(
                    f"""
                    CREATE VECTOR INDEX {vi['name']} IF NOT EXISTS
                    FOR (n:{vi['label']}) ON (n.{vi['property']})
                    OPTIONS {{
                        indexConfig: {{
                            `vector.dimensions`: {vi['dimensions']},
                            `vector.similarity_function`: '{vi['similarity']}'
                        }}
                    }}
                    """
                )
                logger.info("Vector index '%s' ensured.", vi["name"])
            except Exception as exc:
                logger.warning("Vector index %s: %s", vi["name"], exc)

    logger.info("Neo4j schema setup complete.")


def get_graph_stats(driver: Driver) -> dict:
    """Return node and relationship counts."""
    with driver.session() as session:
        node_count = session.run("MATCH (n) RETURN count(n) AS cnt").single()["cnt"]
        rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS cnt").single()["cnt"]
    return {"nodes": node_count, "relationships": rel_count}


def setup_graph(uri: str, user: str, password: str) -> Optional[Driver]:
    """Full setup: ensure container running, connect, apply schema. Returns None if unavailable."""
    ensure_neo4j_running()
    driver = wait_for_neo4j(uri, user, password, timeout=10)
    if driver is not None:
        apply_schema(driver)
    return driver
