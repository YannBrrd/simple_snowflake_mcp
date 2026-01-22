import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import mcp.server.stdio
import mcp.types as types
import snowflake.connector
import yaml
from dotenv import load_dotenv
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from pydantic import AnyUrl

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

def load_config() -> dict[str, Any]:
    """
    Load configuration from config.yaml file.
    Falls back to default values if file is not found or has issues.
    """
    config_file = os.getenv("CONFIG_FILE", "config.yaml")
    config_path = Path(__file__).parent.parent.parent / config_file
    default_config = {
        "logging": {
            "level": "INFO",
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "file_logging": {
                "enabled": False,
                "filename": "logs/server.log",
                "max_bytes": 10485760,
                "backup_count": 5
            }
        },
        "server": {
            "name": "simple_snowflake_mcp",
            "version": "0.2.0",
            "description": "Enhanced Snowflake MCP Server with full protocol compliance",
            "connection": {
                "test_on_startup": True,
                "timeout": 30
            }
        },
        "snowflake": {
            "read_only": True,
            "default_query_limit": 1000,
            "max_query_limit": 50000
        },
        "mcp": {
            "experimental_features": {
                "resource_subscriptions": True,
                "completion_support": False
            },
            "notifications": {
                "resources_changed": True,
                "tools_changed": True,
                "prompts_changed": True
            }
        }
    }

    try:
        if config_path.exists():
            with open(config_path, encoding='utf-8') as f:
                loaded_config = yaml.safe_load(f)
                # Merge with default config to ensure all keys exist
                def merge_configs(default, loaded):
                    for key, value in loaded.items():
                        if key in default and isinstance(default[key], dict) and isinstance(value, dict):
                            merge_configs(default[key], value)
                        else:
                            default[key] = value
                    return default
                return merge_configs(default_config, loaded_config)
        else:
            print(f"Config file not found at {config_path}, using defaults")
            return default_config
    except Exception as e:
        print(f"Error loading config file: {e}, using defaults")
        return default_config

# Load configuration
CONFIG = load_config()

def setup_logging():
    """Setup logging based on configuration."""
    log_config = CONFIG.get("logging", {})

    # Convert string log level to logging constant
    log_level_str = log_config.get("level", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    # Setup basic configuration
    log_format = log_config.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Configure root logger
    logging.basicConfig(level=log_level, format=log_format, force=True)

    # Setup file logging if enabled
    file_config = log_config.get("file_logging", {})
    if file_config.get("enabled", False):
        try:
            from logging.handlers import RotatingFileHandler
            log_file = Path(file_config.get("filename", "logs/server.log"))
            log_file.parent.mkdir(exist_ok=True)

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=file_config.get("max_bytes", 10485760),
                backupCount=file_config.get("backup_count", 5)
            )
            file_handler.setFormatter(logging.Formatter(log_format))
            logging.getLogger().addHandler(file_handler)
        except Exception as e:
            print(f"Failed to setup file logging: {e}")

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)
logger.info(f"Configuration loaded: {CONFIG['server']['name']} v{CONFIG['server']['version']}")

# Store notes as a simple key-value dict to demonstrate state management
notes: dict[str, str] = {}

# Track resource subscriptions for notifications
resource_subscriptions: dict[str, set[str]] = {}

# Server metadata from configuration
SERVER_INFO = {
    "name": CONFIG["server"]["name"],
    "version": CONFIG["server"]["version"],
    "description": CONFIG["server"]["description"],
    "author": "Yann Barraud",
    "license": "MIT"
}

server = Server("simple_snowflake_mcp")

# Configuration Snowflake (à adapter avec vos identifiants)
SNOWFLAKE_CONFIG = {
    "user": os.getenv("SNOWFLAKE_USER"),
    "password": os.getenv("SNOWFLAKE_PASSWORD"),
    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
}

# Ajout dynamique des paramètres optionnels si présents
def _add_optional_snowflake_params(config):
    for opt in [
        ("warehouse", "SNOWFLAKE_WAREHOUSE"),
        ("database", "SNOWFLAKE_DATABASE"),
        ("schema", "SNOWFLAKE_SCHEMA")
    ]:
        val = os.getenv(opt[1])
        if val:
            config[opt[0]] = val
_add_optional_snowflake_params(SNOWFLAKE_CONFIG)

# Ajout d'une variable globale pour le mode read-only par défaut (TRUE par défaut)
# Environment variable overrides config file
MCP_READ_ONLY = os.getenv("MCP_READ_ONLY", str(CONFIG["snowflake"]["read_only"])).lower() == "true"
DEFAULT_QUERY_LIMIT = CONFIG["snowflake"]["default_query_limit"]
MAX_QUERY_LIMIT = CONFIG["snowflake"]["max_query_limit"]

def _safe_snowflake_execute(query: str, description: str = "Query") -> dict[str, Any]:
    """
    Safely execute a Snowflake query with proper error handling and logging.
    """
    try:
        logger.info(f"Executing {description}: {query[:100]}...")
        ctx = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
        cur = ctx.cursor()
        cur.execute(query)

        # Handle different query types
        if cur.description:
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            result = [dict(zip(columns, row)) for row in rows]
        else:
            result = {"status": "success", "rowcount": cur.rowcount}

        cur.close()
        ctx.close()
        logger.info(f"{description} completed successfully")
        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"{description} failed: {str(e)}")
        return {"success": False, "error": str(e), "data": None}

def _format_markdown_table(data: list[dict[str, Any]]) -> str:
    """Format query results as a markdown table."""
    if not data:
        return "No results found."

    columns = list(data[0].keys())
    header = "| " + " | ".join(columns) + " |"
    separator = "|" + "---|" * len(columns)

    rows = []
    for row in data:
        row_str = "| " + " | ".join(str(row.get(col, "")) for col in columns) + " |"
        rows.append(row_str)

    return header + "\n" + separator + "\n" + "\n".join(rows)

@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    """
    List available resources including notes and Snowflake schema information.
    Each resource is exposed with appropriate URI schemes.
    """
    resources = []

    # Add note resources
    for name in notes:
        resources.append(
            types.Resource(
                uri=AnyUrl(f"note://internal/{name}"),
                name=f"Note: {name}",
                description=f"A simple note named {name}",
                mimeType="text/plain",
            )
        )

    # Add Snowflake schema resource
    resources.append(
        types.Resource(
            uri=AnyUrl("snowflake://schema/metadata"),
            name="Snowflake Schema Metadata",
            description="Comprehensive Snowflake database schema information",
            mimeType="application/json",
        )
    )

    # Add connection status resource
    resources.append(
        types.Resource(
            uri=AnyUrl("snowflake://status/connection"),
            name="Snowflake Connection Status",
            description="Current Snowflake connection status and configuration",
            mimeType="application/json",
        )
    )

    logger.info(f"Listed {len(resources)} resources")
    return resources

@server.read_resource()
async def handle_read_resource(uri: AnyUrl) -> str:
    """
    Read a specific resource's content by its URI.
    Supports multiple URI schemes: note://, snowflake://
    """
    logger.info(f"Reading resource: {uri}")

    if uri.scheme == "note":
        name = uri.path
        if name is not None:
            name = name.lstrip("/")
            if name in notes:
                return notes[name]
        raise ValueError(f"Note not found: {name}")

    elif uri.scheme == "snowflake":
        if str(uri) == "snowflake://schema/metadata":
            # Return comprehensive schema metadata
            result = _safe_snowflake_execute("SHOW DATABASES", "Schema metadata query")
            if result["success"]:
                return json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    "server_info": SERVER_INFO,
                    "databases": result["data"],
                    "connection_config": {k: v for k, v in SNOWFLAKE_CONFIG.items() if k != "password"}
                }, indent=2)
            else:
                return json.dumps({"error": result["error"]}, indent=2)

        elif str(uri) == "snowflake://status/connection":
            # Return connection status
            result = _safe_snowflake_execute("SELECT CURRENT_VERSION(), CURRENT_TIMESTAMP()", "Connection status")
            if result["success"]:
                status_data = {
                    "status": "connected",
                    "timestamp": datetime.now().isoformat(),
                    "snowflake_info": result["data"][0] if result["data"] else {},
                    "server_config": SERVER_INFO,
                    "read_only_mode": MCP_READ_ONLY
                }
            else:
                status_data = {
                    "status": "error",
                    "timestamp": datetime.now().isoformat(),
                    "error": result["error"],
                    "read_only_mode": MCP_READ_ONLY
                }
            return json.dumps(status_data, indent=2)

    raise ValueError(f"Unsupported URI scheme or path: {uri}")

@server.subscribe_resource()
async def handle_subscribe_resource(uri: AnyUrl) -> None:
    """
    Subscribe to resource updates.
    Clients will be notified when the resource changes.
    """
    uri_str = str(uri)
    logger.info(f"Subscribing to resource updates: {uri_str}")

    if uri_str not in resource_subscriptions:
        resource_subscriptions[uri_str] = set()

    # Add client to subscription (in a real implementation, you'd track client IDs)
    resource_subscriptions[uri_str].add("default_client")

@server.unsubscribe_resource()
async def handle_unsubscribe_resource(uri: AnyUrl) -> None:
    """
    Unsubscribe from resource updates.
    """
    uri_str = str(uri)
    logger.info(f"Unsubscribing from resource updates: {uri_str}")

    if uri_str in resource_subscriptions:
        resource_subscriptions[uri_str].discard("default_client")
        if not resource_subscriptions[uri_str]:
            del resource_subscriptions[uri_str]

@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    """
    List available prompts with comprehensive argument definitions.
    Each prompt can have optional arguments to customize behavior.
    """
    return [
        types.Prompt(
            name="summarize-notes",
            description="Creates a summary of all notes",
            arguments=[
                types.PromptArgument(
                    name="style",
                    description="Style of the summary (brief/detailed/executive)",
                    required=False,
                ),
                types.PromptArgument(
                    name="format",
                    description="Output format (text/markdown/json)",
                    required=False,
                )
            ],
        ),
        types.Prompt(
            name="analyze-snowflake-schema",
            description="Analyze and summarize Snowflake database schema",
            arguments=[
                types.PromptArgument(
                    name="database",
                    description="Specific database to analyze (optional)",
                    required=False,
                ),
                types.PromptArgument(
                    name="focus",
                    description="Analysis focus (tables/views/functions/all)",
                    required=False,
                )
            ],
        ),
        types.Prompt(
            name="generate-sql-query",
            description="Generate SQL query suggestions based on schema",
            arguments=[
                types.PromptArgument(
                    name="intent",
                    description="What you want to accomplish with the query",
                    required=True,
                ),
                types.PromptArgument(
                    name="complexity",
                    description="Query complexity level (simple/intermediate/advanced)",
                    required=False,
                )
            ],
        ),
        types.Prompt(
            name="troubleshoot-connection",
            description="Help troubleshoot Snowflake connection issues",
            arguments=[
                types.PromptArgument(
                    name="error_message",
                    description="Error message or symptoms you're experiencing",
                    required=False,
                )
            ],
        )
    ]

@server.get_prompt()
async def handle_get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    """
    Generate a prompt by combining arguments with server state.
    Supports multiple prompt types with dynamic content generation.
    """
    logger.info(f"Generating prompt: {name} with arguments: {arguments}")
    args = arguments or {}

    if name == "summarize-notes":
        style = args.get("style", "brief")
        format_type = args.get("format", "text")
        detail_prompt = " Give extensive details." if style == "detailed" else ""
        if style == "executive":
            detail_prompt = " Provide executive summary with key insights and actionable items."

        notes_content = "\n".join(f"- {name}: {content}" for name, content in notes.items())
        if not notes_content:
            notes_content = "No notes available."

        base_text = f"Here are the current notes to summarize:{detail_prompt}\n\n{notes_content}"
        if format_type == "json":
            base_text += "\n\nPlease format the response as JSON."
        elif format_type == "markdown":
            base_text += "\n\nPlease format the response as markdown."

        return types.GetPromptResult(
            description="Summarize the current notes",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=base_text),
                )
            ],
        )

    elif name == "analyze-snowflake-schema":
        database = args.get("database", "all databases")
        focus = args.get("focus", "all")

        # Get schema information
        result = _safe_snowflake_execute("SHOW DATABASES", "Schema analysis")
        schema_info = result["data"] if result["success"] else [{"error": result["error"]}]

        prompt_text = f"""Analyze the following Snowflake database schema information:
        
Target: {database}
Focus: {focus}
        
Schema Information:
{json.dumps(schema_info, indent=2)}

Please provide insights about:
- Database structure and organization  
- Table/view relationships (if focus includes tables/views)
- Data patterns and potential optimizations
- Recommended queries or analysis approaches
"""

        return types.GetPromptResult(
            description="Analyze Snowflake database schema",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt_text),
                )
            ],
        )

    elif name == "generate-sql-query":
        intent = args.get("intent", "general analysis")
        complexity = args.get("complexity", "simple")

        # Get current schema context
        result = _safe_snowflake_execute("SHOW DATABASES", "Query generation context")
        schema_context = result["data"] if result["success"] else []

        prompt_text = f"""Generate SQL queries for Snowflake based on the following requirements:

Intent: {intent}
Complexity Level: {complexity}

Available Schema Context:
{json.dumps(schema_context, indent=2)}

Please provide:
1. One or more SQL queries that accomplish the intent
2. Explanation of what each query does
3. Any assumptions made about the data structure
4. Performance considerations (if complexity is intermediate/advanced)

Ensure queries are compatible with Snowflake SQL dialect.
"""

        return types.GetPromptResult(
            description="Generate SQL query suggestions",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt_text),
                )
            ],
        )

    elif name == "troubleshoot-connection":
        error_msg = args.get("error_message", "general connection issues")

        # Get connection status
        status_result = _safe_snowflake_execute("SELECT CURRENT_VERSION()", "Connection test")
        connection_status = "Connected successfully" if status_result["success"] else f"Connection failed: {status_result['error']}"

        prompt_text = f"""Help troubleshoot Snowflake connection issues:

Error/Symptoms: {error_msg}
Current Connection Status: {connection_status}
Server Configuration: {SERVER_INFO}
Read-Only Mode: {MCP_READ_ONLY}

Configuration (sensitive data removed):
{json.dumps({k: v for k, v in SNOWFLAKE_CONFIG.items() if k != "password"}, indent=2)}

Please provide:
1. Likely causes of the issue
2. Step-by-step troubleshooting guide
3. Common solutions
4. How to verify the fix
5. Prevention tips for the future
"""

        return types.GetPromptResult(
            description="Troubleshoot Snowflake connection issues",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt_text),
                )
            ],
        )

    raise ValueError(f"Unknown prompt: {name}")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """
    List available tools with comprehensive JSON Schema validation.
    Each tool specifies detailed arguments and validation rules.
    """
    return [
        types.Tool(
            name="get-connection-info",
            description="Get current Snowflake connection information and server status",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False
            },
        ),
        types.Tool(
            name="add-note",
            description="Add or update a note for future reference",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "description": "Note name/identifier"},
                    "content": {"type": "string", "minLength": 1, "description": "Note content"}
                },
                "required": ["name", "content"],
                "additionalProperties": False
            },
        ),
        types.Tool(
            name="delete-note",
            description="Delete an existing note",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "description": "Note name to delete"}
                },
                "required": ["name"],
                "additionalProperties": False
            },
        ),
        types.Tool(
            name="execute-snowflake-sql",
            description="Execute a SQL query on Snowflake and return the result as JSON",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL query to execute",
                        "minLength": 1,
                        "examples": ["SELECT CURRENT_TIMESTAMP()", "SHOW DATABASES"]
                    },
                    "format": {
                        "type": "string",
                        "enum": ["json", "markdown", "csv"],
                        "default": "json",
                        "description": "Output format for results"
                    }
                },
                "required": ["sql"],
                "additionalProperties": False
            },
        ),
        types.Tool(
            name="list-snowflake-warehouses",
            description="List available Data Warehouses (DWH) on Snowflake with detailed information",
            inputSchema={
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include detailed warehouse information"
                    }
                },
                "additionalProperties": False
            },
        ),
        types.Tool(
            name="list-databases",
            description="List all accessible Snowflake databases with optional filtering",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Filter databases by name pattern (supports wildcards)",
                        "examples": ["PROD_%", "%_DEV"]
                    },
                    "include_details": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include database details and metadata"
                    }
                },
                "additionalProperties": False
            },
        ),
        types.Tool(
            name="execute-query",
            description="Execute a SQL query with read-only protection and flexible output format",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SQL query to execute", "minLength": 1},
                    "read_only": {"type": "boolean", "default": True, "description": "Allow only read-only queries"},
                    "format": {"type": "string", "enum": ["markdown", "json", "csv"], "default": "markdown"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50000, "description": "Maximum rows to return"}
                },
                "required": ["sql"],
                "additionalProperties": False
            },
        ),
        types.Tool(
            name="export-schema",
            description="Export database schema information in various formats",
            inputSchema={
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database to export (optional - exports all if not specified)"},
                    "format": {"type": "string", "enum": ["json", "yaml", "sql"], "default": "json"},
                    "include_data_samples": {"type": "boolean", "default": False, "description": "Include sample data"}
                },
                "additionalProperties": False
            },
        )
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """
    Handle tool execution requests with comprehensive error handling and validation.
    Tools can modify server state and notify clients of changes.
    """
    logger.info(f"Executing tool: {name} with arguments: {arguments}")
    args = arguments or {}

    try:
        # Connection and metadata tools
        if name == "get-connection-info":
            result = _safe_snowflake_execute("SELECT CURRENT_VERSION(), CURRENT_USER(), CURRENT_DATABASE(), CURRENT_SCHEMA(), CURRENT_WAREHOUSE()", "Connection info")
            if result["success"]:
                info = {
                    "server_info": SERVER_INFO,
                    "connection_status": "connected",
                    "snowflake_info": result["data"][0] if result["data"] else {},
                    "config": {k: v for k, v in SNOWFLAKE_CONFIG.items() if k != "password"},
                    "read_only_mode": MCP_READ_ONLY,
                    "timestamp": datetime.now().isoformat()
                }
                return [types.TextContent(type="text", text=json.dumps(info, indent=2))]
            else:
                return [types.TextContent(type="text", text=f"Connection error: {result['error']}")]

        # Note management tools
        elif name == "add-note":
            note_name = args.get("name")
            content = args.get("content")
            if not note_name or not content:
                raise ValueError("Both 'name' and 'content' are required")

            old_content = notes.get(note_name)
            notes[note_name] = content

            return [types.TextContent(type="text", text=f"Note '{note_name}' {'updated' if old_content else 'created'} successfully")]

        elif name == "delete-note":
            note_name = args.get("name")
            if not note_name:
                raise ValueError("'name' parameter is required")

            if note_name in notes:
                del notes[note_name]
                return [types.TextContent(type="text", text=f"Note '{note_name}' deleted successfully")]
            else:
                return [types.TextContent(type="text", text=f"Note '{note_name}' not found")]

        # Enhanced Snowflake tools
        elif name == "execute-snowflake-sql":
            sql = args.get("sql")
            format_type = args.get("format", "json")
            if not sql:
                raise ValueError("'sql' parameter is required")

            result = _safe_snowflake_execute(sql, "SQL execution")
            if result["success"]:
                if format_type == "markdown" and isinstance(result["data"], list):
                    output = _format_markdown_table(result["data"])
                elif format_type == "csv" and isinstance(result["data"], list):
                    if result["data"]:
                        import csv
                        import io
                        output_buffer = io.StringIO()
                        writer = csv.DictWriter(output_buffer, fieldnames=result["data"][0].keys())
                        writer.writeheader()
                        writer.writerows(result["data"])
                        output = output_buffer.getvalue()
                    else:
                        output = "No data returned"
                else:
                    output = json.dumps(result["data"], indent=2, default=str)
                return [types.TextContent(type="text", text=output)]
            else:
                return [types.TextContent(type="text", text=f"Snowflake error: {result['error']}")]

        elif name == "list-snowflake-warehouses":
            include_details = args.get("include_details", True)
            query = "SHOW WAREHOUSES"
            result = _safe_snowflake_execute(query, "List warehouses")
            if result["success"]:
                if include_details:
                    output = json.dumps(result["data"], indent=2, default=str)
                else:
                    warehouses = [row.get("name", "") for row in result["data"]]
                    output = "\n".join(warehouses)
                return [types.TextContent(type="text", text=output)]
            else:
                return [types.TextContent(type="text", text=f"Snowflake error: {result['error']}")]

        elif name == "list-databases":
            pattern = args.get("pattern")
            include_details = args.get("include_details", False)

            query = "SHOW DATABASES"
            if pattern:
                query += f" LIKE '{pattern}'"

            result = _safe_snowflake_execute(query, "List databases")
            if result["success"]:
                if include_details:
                    output = json.dumps(result["data"], indent=2, default=str)
                else:
                    databases = [row.get("name", "") for row in result["data"]]
                    output = "\n".join(databases)
                return [types.TextContent(type="text", text=output)]
            else:
                return [types.TextContent(type="text", text=f"Snowflake error: {result['error']}")]

        elif name == "execute-query":
            sql = args.get("sql")
            if not sql:
                raise ValueError("'sql' parameter is required")

            read_only = args.get("read_only", MCP_READ_ONLY)
            format_type = args.get("format", "markdown")
            limit = args.get("limit")

            # Check if query is allowed in read-only mode
            allowed_commands = ["SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH"]
            first_word = sql.strip().split()[0].upper() if sql.strip() else ""

            if read_only and first_word not in allowed_commands:
                return [types.TextContent(type="text", text="Only read-only queries are allowed in read-only mode.")]

            # Apply limit if specified, or use default for SELECT queries
            if limit:
                # Validate limit doesn't exceed maximum
                if limit > MAX_QUERY_LIMIT:
                    limit = MAX_QUERY_LIMIT
                    logger.warning(f"Query limit reduced from {args.get('limit')} to maximum {MAX_QUERY_LIMIT}")
            elif first_word == "SELECT" and "LIMIT" not in sql.upper():
                # Apply default limit for SELECT queries without explicit limit
                limit = DEFAULT_QUERY_LIMIT
                logger.info(f"Applying default limit {DEFAULT_QUERY_LIMIT} to SELECT query")

            if limit and "LIMIT" not in sql.upper():
                sql += f" LIMIT {limit}"

            result = _safe_snowflake_execute(sql, "Execute query")
            if result["success"]:
                if format_type == "markdown":
                    output = _format_markdown_table(result["data"])
                else:
                    output = json.dumps(result["data"], indent=2, default=str)
                return [types.TextContent(type="text", text=output)]
            else:
                return [types.TextContent(type="text", text=f"Snowflake error: {result['error']}")]

        elif name == "export-schema":
            database = args.get("database")
            format_type = args.get("format", "json")
            include_samples = args.get("include_data_samples", False)

            schema_data = {"exported_at": datetime.now().isoformat(), "server_info": SERVER_INFO}

            # Get database information
            if database:
                db_query = f"SHOW DATABASES LIKE '{database}'"
            else:
                db_query = "SHOW DATABASES"

            db_result = _safe_snowflake_execute(db_query, "Export schema - databases")
            if not db_result["success"]:
                return [types.TextContent(type="text", text=f"Error getting database info: {db_result['error']}")]

            schema_data["databases"] = db_result["data"]

            if format_type == "json":
                output = json.dumps(schema_data, indent=2, default=str)
            elif format_type == "yaml":
                try:
                    import yaml
                    output = yaml.dump(schema_data, default_flow_style=False)
                except ImportError:
                    output = json.dumps(schema_data, indent=2, default=str) + "\n\n# Note: YAML format requires PyYAML package"
            else:  # SQL format
                output = f"-- Schema export generated at {schema_data['exported_at']}\n"
                output += f"-- Server: {SERVER_INFO['name']} v{SERVER_INFO['version']}\n\n"
                for db in schema_data["databases"]:
                    output += f"-- Database: {db.get('name', 'Unknown')}\n"

            return [types.TextContent(type="text", text=output)]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        logger.error(f"Tool execution failed: {name} - {str(e)}")
        return [types.TextContent(type="text", text=f"Tool execution error: {str(e)}")]

async def test_snowflake_connection():
    """Test Snowflake connection for debugging purposes."""
    result = _safe_snowflake_execute("SELECT CURRENT_TIMESTAMP()", "Connection test")
    if result["success"]:
        logger.info(f"Snowflake connection OK, CURRENT_TIMESTAMP: {result['data'][0] if result['data'] else 'No data'}")
    else:
        logger.error(f"Snowflake connection error: {result['error']}")

async def main():
    """Main entry point for the MCP server."""
    logger.info(f"Starting {SERVER_INFO['name']} v{SERVER_INFO['version']}")
    logger.info(f"Configuration: Read-only mode: {MCP_READ_ONLY}, Log level: {CONFIG['logging']['level']}")

    # Test connection on startup if configured
    if CONFIG["server"]["connection"]["test_on_startup"]:
        await test_snowflake_connection()

    # Get notification and experimental capabilities from config
    mcp_config = CONFIG["mcp"]
    notifications = mcp_config["notifications"]
    experimental = mcp_config["experimental_features"]

    # Build experimental capabilities dict
    experimental_caps = {}
    if experimental.get("resource_subscriptions", False):
        experimental_caps["resourceSubscriptions"] = True
    if experimental.get("completion_support", False):
        experimental_caps["completionSupport"] = True

    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_INFO['name'],
                server_version=SERVER_INFO['version'],
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(
                        resources_changed=notifications.get("resources_changed", True),
                        tools_changed=notifications.get("tools_changed", True),
                        prompts_changed=notifications.get("prompts_changed", True),
                    ),
                    experimental_capabilities=experimental_caps,
                ),
            ),
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
