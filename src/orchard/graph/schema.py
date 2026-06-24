"""
Ladybug DDL statements for the Orchard Apple Semantic Graph schema.

All CREATE statements use IF NOT EXISTS so init_schema() is idempotent.
"""

NODE_TABLES: list[str] = [
    """CREATE NODE TABLE IF NOT EXISTS BuildSnapshot(
        id STRING PRIMARY KEY,
        build_system STRING,
        workspace_root STRING,
        derived_data_path STRING,
        index_store_path STRING,
        toolchain_id STRING,
        commit_sha STRING,
        created_at STRING,
        build_config_hash STRING,
        sdk STRING,
        configuration STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Module(
        name STRING PRIMARY KEY,
        language STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Target(
        id STRING PRIMARY KEY,
        name STRING,
        platform STRING,
        sdk STRING,
        triple STRING,
        configuration STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS File(
        path STRING PRIMARY KEY,
        module STRING,
        language STRING,
        target_id STRING,
        is_generated BOOLEAN
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Symbol(
        id STRING PRIMARY KEY,
        usr STRING,
        precise_id STRING,
        name STRING,
        language STRING,
        kind STRING,
        module STRING,
        target_id STRING,
        file_path STRING,
        signature STRING,
        container_usr STRING,
        access_level STRING,
        origin STRING,
        is_generated BOOLEAN
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Occurrence(
        id STRING PRIMARY KEY,
        usr STRING,
        file_path STRING,
        line INT64,
        col INT64,
        role STRING
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Chunk(
        id STRING PRIMARY KEY,
        owner_usr STRING,
        chunk_kind STRING,
        content STRING,
        embedding FLOAT[768]
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Diagnostic(
        id STRING PRIMARY KEY,
        phase STRING,
        severity STRING,
        code STRING,
        message STRING
    )""",
]

REL_TABLES: list[str] = [
    "CREATE REL TABLE IF NOT EXISTS ContainsFile(FROM Module TO File)",
    "CREATE REL TABLE IF NOT EXISTS ContainsTarget(FROM Module TO Target)",
    "CREATE REL TABLE IF NOT EXISTS BuiltTarget(FROM BuildSnapshot TO Target)",
    "CREATE REL TABLE IF NOT EXISTS ObservedFile(FROM BuildSnapshot TO File)",
    "CREATE REL TABLE IF NOT EXISTS Declares(FROM File TO Symbol)",
    "CREATE REL TABLE IF NOT EXISTS ContainsChunk(FROM Symbol TO Chunk)",
    "CREATE REL TABLE IF NOT EXISTS ContainsOccurrence(FROM File TO Occurrence)",
    "CREATE REL TABLE IF NOT EXISTS RefersTo(FROM Occurrence TO Symbol, role STRING)",
    """CREATE REL TABLE IF NOT EXISTS Calls(
        FROM Symbol TO Symbol,
        source STRING,
        confidence DOUBLE,
        provenance STRING,
        build_id STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS References(
        FROM Symbol TO Symbol,
        source STRING,
        confidence DOUBLE
    )""",
    "CREATE REL TABLE IF NOT EXISTS Inherits(FROM Symbol TO Symbol, source STRING)",
    "CREATE REL TABLE IF NOT EXISTS Implements(FROM Symbol TO Symbol, source STRING)",
    "CREATE REL TABLE IF NOT EXISTS Imports(FROM File TO File, kind STRING)",
    "CREATE REL TABLE IF NOT EXISTS ConformsTo(FROM Symbol TO Symbol, source STRING)",
    """CREATE REL TABLE IF NOT EXISTS BridgesTo(
        FROM Symbol TO Symbol,
        bridge_kind STRING,
        provenance STRING,
        confidence DOUBLE,
        build_id STRING
    )""",
    "CREATE REL TABLE IF NOT EXISTS ProducedDiagnostic(FROM BuildSnapshot TO Diagnostic)",
]

SCHEMA_STATEMENTS: list[str] = NODE_TABLES + REL_TABLES
