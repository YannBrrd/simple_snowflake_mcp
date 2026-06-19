# Simple Snowflake MCP server

**Enhanced Snowflake MCP Server with comprehensive configuration system and full MCP protocol compliance.**

A production-ready MCP server that provides seamless Snowflake integration with advanced features including configurable logging, resource subscriptions, and comprehensive error handling. Designed to work seamlessly behind corporate proxies.

For release details, see [CHANGELOG.md](CHANGELOG.md).

### Tools

The server exposes the following MCP tools to interact with Snowflake:

**Database Operations:**
- **execute-snowflake-sql**: Executes a SQL query on Snowflake and returns the result. Supports `json` (default), `markdown`, and `csv` output via the `format` argument.
- **execute-query**: Executes a SQL query with server-enforced read-only protection. In read-only mode (the default) only `SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`, and `WITH` statements (without DML) are allowed. Read-only mode is governed **solely by server configuration** and cannot be relaxed by the caller. Supports a `limit` and `markdown` (default)/`json`/`csv` output via `format`.

**Discovery and Metadata:**
- **get-connection-info**: Returns current Snowflake connection information and server status.
- **list-snowflake-warehouses**: Lists available Data Warehouses (DWH) on Snowflake. Pass `include_details: false` for names only.
- **list-databases**: Lists all accessible Snowflake databases. Supports a `pattern` filter (wildcards) and `include_details`.
- **list-schemas**: Lists schemas in a `database`. Supports a `pattern` filter (wildcards) and `include_details`.
- **list-tables**: Lists tables in a `database`/`schema`. Supports a `pattern` filter (wildcards) and `include_details`.
- **list-views**: Lists views in a `database`/`schema`. Supports a `pattern` filter (wildcards) and `include_details`.
- **describe-table**: Returns the columns and types of a `database`/`schema`/`table` (works for views too). Supports `json` (default)/`markdown`/`csv` via `format`.
- **query-view**: Reads rows from a `database`/`schema`/`view` (or table) by name, with the same server-enforced read-only protection and row limiting as `execute-query`. Supports a `limit` and `markdown` (default)/`json`/`csv` via `format`.
- **export-schema**: Exports hierarchical schema metadata (databases → schemas → tables/views → columns). Supports `json` (default), `yaml`, and `sql` via `format`, an optional `database` filter, and opt-in `include_data_samples` (table rows only, max 3 rows per table).

**Notes (in-memory session state):**
- **add-note**: Adds or updates a note (`name`, `content`) kept in server memory for the session.
- **delete-note**: Deletes an existing note by `name`.
- **list-notes**: Lists current note names in sorted order.
- **get-note**: Returns the note payload (`name`, `content`) for a given note name.

### Prompts

The server also exposes MCP prompts that bundle server context into ready-to-use
messages:

- **summarize-notes**: Summarizes the stored notes. Arguments: `style` (`brief`/`detailed`/`executive`), `format` (`text`/`markdown`/`json`).
- **analyze-snowflake-schema**: Produces a schema-analysis prompt. Arguments: `database` (optional focus), `focus` (`tables`/`views`/`functions`/`all`).
- **generate-sql-query**: Helps draft a SQL query from a natural-language goal. Arguments: `intent` (required), `complexity` (`simple`/`intermediate`/`advanced`).
- **troubleshoot-connection**: Builds a connection-troubleshooting prompt. Arguments: `error_message` (optional).

## 🔒 Security Model

This server executes client-supplied SQL against Snowflake using a single set of
credentials. Treat the MCP client as untrusted (an LLM can be prompt-injected) and
deploy accordingly.

> **The real security boundary is a least-privilege Snowflake role, not the
> server's keyword filter.** The built-in read-only check is *defense-in-depth
> only*. Always connect with a role scoped to exactly what you need.

**Required deployment posture:**

1. **Use a least-privilege, read-only Snowflake role.** For read-only deployments,
   grant only `USAGE`/`SELECT` (and the relevant `SHOW`/`DESCRIBE` visibility) — no
   `INSERT`/`UPDATE`/`DELETE`/DDL/`GRANT`. If the role cannot write, no bypass of the
   keyword filter can cause damage.
2. **Keep `read_only: true`** (the default). Read-only mode is governed solely by
   server configuration / the `MCP_READ_ONLY` environment variable. It is **not**
   client-controllable — there is no `read_only` tool argument.
3. **Set a statement timeout and rate limit** (see `config.yaml`) to bound runaway
   or abusive queries and warehouse-credit consumption.

**What the server enforces:**

- Read-only mode applies to **every** SQL-executing tool through a single guard;
  comments are stripped, multi-statement input and CTE-fronted DML (e.g.
  `WITH ... DELETE`) are rejected.
- `pattern` / `database` arguments are validated against a strict allow-list before
  being placed into `LIKE` clauses; `limit` is coerced to a bounded integer and
  applied at the driver, never concatenated into SQL.
- Row-producing reads without an explicit `LIMIT` are capped at
  `default_query_limit` rows (applied at the driver). When a result is capped the
  response includes an explicit *"results were truncated"* notice — rows are never
  dropped silently. Pass a larger `limit` (up to `max_query_limit`) or add your own
  `LIMIT` clause to retrieve more.
- Snowflake errors are **not** returned verbatim to the client; a generic message
  with a reference id is returned and full detail is logged server-side.
- Query text is not logged at `INFO` (only a length + hash); full SQL is `DEBUG`-only.

## 🆕 Configuration System

The server now includes a comprehensive YAML-based configuration system that allows you to customize all aspects of the server behavior.

### Configuration File Structure

Create a `config.yaml` file in your project root:

```yaml
# Logging Configuration
logging:
  level: INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL (overridable via LOG_LEVEL)
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  file_logging:
    enabled: false        # Set to true to enable file logging
    filename: "logs/server.log"  # Must resolve under repository ./logs/
    max_bytes: 10485760   # Rotate after 10 MB
    backup_count: 5

# Server Configuration
server:
  name: "simple_snowflake_mcp"
  version: "0.4.0"
  description: "Enhanced Snowflake MCP Server with full protocol compliance"
  connection:
    test_on_startup: true
    timeout: 30

# Snowflake Configuration
snowflake:
  # Read-only mode is read from here (and the MCP_READ_ONLY env var), NOT from
  # the server block. Set to false to allow write operations.
  read_only: true
  default_query_limit: 1000
  max_query_limit: 50000
  statement_timeout_seconds: 300
  connection_reuse: true

# Security controls
security:
  rate_limit:
    enabled: true
    max_calls: 60
    window_seconds: 60
  notes:
    max_count: 100
    max_content_length: 10000

# MCP Protocol Settings
mcp:
  experimental_features:
    resource_subscriptions: true  # Enable resource change notifications
    completion_support: false    # Set to true when MCP version supports it
  
  notifications:
    resources_changed: true
    tools_changed: true
    prompts_changed: true
```

### Using Custom Configuration

You can specify a custom configuration file using the `CONFIG_FILE` environment variable:

**Windows:**
```cmd
set CONFIG_FILE=config_debug.yaml
python -m simple_snowflake_mcp
```

**Linux/macOS:**
```bash
CONFIG_FILE=config_production.yaml python -m simple_snowflake_mcp
```

### Configuration Override Priority

Configuration values are resolved in this order (highest to lowest priority):
1. Environment variables (e.g., `LOG_LEVEL`, `MCP_READ_ONLY`)
2. Custom configuration file (via `CONFIG_FILE`)
3. Default `config.yaml` file
4. Built-in defaults

## 🚀 Quick Install

### Method 1: Install with `uvx` (Recommended)

```bash
# Install and run directly
uvx simple-snowflake-mcp
```

### Method 2: Install from source

```bash
# Clone the repo
git clone https://github.com/YannBrrd/simple_snowflake_mcp
cd simple_snowflake_mcp

# Install with uv (creates a venv automatically)
uv sync

# Run
uv run simple-snowflake-mcp
```

### Method 3: Development

```bash
# Install with development dependencies
uv sync --all-extras

# Run the tests
uv run pytest

# Lint with ruff
uv run ruff check .
uv run ruff format .
```

### Configuration Claude Desktop

On MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`

On Windows: `%APPDATA%/Claude/claude_desktop_config.json`

<details>
  <summary>Development/Unpublished Servers Configuration</summary>


  ```json
  "mcpServers": {
    "simple_snowflake_mcp": {
      "command": "uv",
      "args": [
        "--directory",
        ".", 
        "run",
        "simple_snowflake_mcp"
      ]
    }
  }
  ```
</details>

<details>
  <summary>Published Servers Configuration</summary>

  ```json
  "mcpServers": {
    "simple_snowflake_mcp": {
      "command": "uvx",
      "args": [
        "simple_snowflake_mcp"
      ]
    }
  }
  ```
</details>

## Docker Setup

### Prerequisites

- Docker and Docker Compose installed on your system
- Your Snowflake credentials

### Quick Start with Docker

1. **Clone the repository**
   ```bash
   git clone <your-repo>
   cd simple_snowflake_mcp
   ```

2. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your Snowflake credentials
   ```

3. **Build and run with Docker Compose**
   ```bash
   # Build the Docker image
   docker-compose build
   
   # Start the service
   docker-compose up -d
   
   # View logs
   docker-compose logs -f
   ```

### Docker Commands

Using Docker Compose directly:
```bash
# Build the image
docker-compose build

# Start in production mode
docker-compose up -d

# Start in development mode (with volume mounts for live code changes)
docker-compose --profile dev up simple-snowflake-mcp-dev -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down

# Clean up (remove containers, images, and volumes)
docker-compose down --rmi all --volumes --remove-orphans
```

Using the provided Makefile (Windows users can use `make` with WSL or install make for Windows):
```bash
# See all available commands
make help

# Build and start
make build
make up

# Development mode
make dev-up

# View logs
make logs

# Clean up
make clean
```

### Docker Configuration

The Docker setup includes:

- **Dockerfile**: Multi-stage build with Python 3.11 slim base image
- **docker-compose.yml**: Service definition with environment variable support
- **.dockerignore**: Optimized build context
- **Makefile**: Convenient commands for Docker operations

#### Environment Variables

All Snowflake configuration can be set via environment variables:

**Required:**
- `SNOWFLAKE_USER`: Your Snowflake username
- `SNOWFLAKE_PASSWORD`: Your Snowflake password
- `SNOWFLAKE_ACCOUNT`: Your Snowflake account identifier

**Optional:**
- `SNOWFLAKE_WAREHOUSE`: Warehouse name
- `SNOWFLAKE_DATABASE`: Default database
- `SNOWFLAKE_SCHEMA`: Default schema
- `MCP_READ_ONLY`: Set to "TRUE" for read-only mode (default: TRUE)

**Configuration System (v0.2.0):**
- `CONFIG_FILE`: Path to custom configuration file (default: config.yaml)
- `LOG_LEVEL`: Override logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

#### Development Mode

For development, use the development profile which mounts your source code:

```bash
docker-compose --profile dev up simple-snowflake-mcp-dev -d
```

This allows you to make changes to the code without rebuilding the Docker image.

## Development

### Installing dependencies

```bash
# Sync all dependencies (prod + dev)
uv sync --all-extras

# Update dependencies
uv lock --upgrade

# Add a new dependency
uv add <package-name>

# Add a dev dependency
uv add --dev <package-name>
```

### Build and Publish

```bash
# Build
uv build

# Publish to PyPI
uv publish --token $UV_PUBLISH_TOKEN
```

### CI

GitHub Actions CI runs on pushes to `main` and on pull requests via `.github/workflows/ci.yml`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

### Debugging with MCP Inspector

Since MCP servers run over stdio, debugging can be challenging. For the best debugging
experience, we strongly recommend using the [MCP Inspector](https://github.com/modelcontextprotocol/inspector).

You can launch the MCP Inspector via [`npm`](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm) with this command:

```bash
npx @modelcontextprotocol/inspector uv run simple-snowflake-mcp
```

Upon launching, the Inspector will display a URL that you can access in your browser to begin debugging.

## New Feature: Snowflake SQL Execution

The server exposes an MCP tool `execute-snowflake-sql` to execute a SQL query on Snowflake and return the result.

### Usage

Call the MCP tool `execute-snowflake-sql` with a `sql` argument containing the SQL query to execute. The result will be returned as a list of dictionaries (one per row).

Example:
```json
{
  "name": "execute-snowflake-sql",
  "arguments": { "sql": "SELECT CURRENT_TIMESTAMP;" }
}
```

The result will be returned in the MCP response.

## Installation and configuration in VS Code

1. **Clone the project and install dependencies**
   ```sh
   git clone https://github.com/YannBrrd/simple_snowflake_mcp
   cd simple_snowflake_mcp

   # Install with uv (creates a venv automatically)
   uv sync --all-extras
   ```

2. **Configure Snowflake access**
   - Copy `.env.example` to `.env` and fill in your credentials:
     ```env
     SNOWFLAKE_USER=...
     SNOWFLAKE_PASSWORD=...
     SNOWFLAKE_ACCOUNT=...
     # SNOWFLAKE_WAREHOUSE   Optional: Snowflake warehouse name
     # SNOWFLAKE_DATABASE    Optional: default database name
     # SNOWFLAKE_SCHEMA      Optional: default schema name
     # MCP_READ_ONLY=true|false   Optional: true/false to force read-only mode
     ```

3. **Configure the server (v0.2.0)**
   - If no `config.yaml` is present, the server uses its built-in defaults (no file is created automatically)
   - Customize logging, limits, and MCP features by editing `config.yaml` (an example is provided in the repository)
   - Use `CONFIG_FILE=custom_config.yaml` to specify a different file (resolved **within the repository only**, to prevent path traversal)

4. **Configure VS Code for MCP debugging**
   - The `.vscode/mcp.json` file is already present:
     ```json
     {
       "servers": {
         "simple-snowflake-mcp": {
           "type": "stdio",
           "command": "uv",
           "args": ["run", "simple-snowflake-mcp"]
         }
       }
     }
     ```
   - Open the command palette (Ctrl+Shift+P), type `MCP: Start Server`, and select `simple-snowflake-mcp`.

5. **Usage**
   - The exposed MCP tools let you query Snowflake (list-databases, list-snowflake-warehouses, execute-query, execute-snowflake-sql, export-schema, etc.).
   - For more examples, see the MCP protocol documentation: https://github.com/modelcontextprotocol/create-python-server

## Enhanced MCP Features (v0.2.0)

### Advanced MCP Protocol Support

This server now implements comprehensive MCP protocol features:

**🔔 Resource Subscriptions**
- Real-time notifications when Snowflake resources change
- Automatic updates for database schema changes
- Tool availability notifications

**📋 Enhanced Resource Management**
- Dynamic resource discovery and listing
- Detailed resource metadata and descriptions  
- Resource templates for browsing the object hierarchy by URI
  (`snowflake://database/{database}/schemas`,
  `snowflake://database/{database}/schema/{schema}/tables`,
  `snowflake://table/{database}/{schema}/{table}`)
- Argument completion for prompts and resource templates (live database/
  schema/table name suggestions)
- Client-controlled log verbosity via the MCP `logging/setLevel` request

**⚡ Performance & Reliability**
- Configurable query limits and a server-side statement timeout
- Comprehensive error handling with generic client messages and a server-side reference id
- Single-connection reuse with automatic reconnect on a stale connection

**🔧 Development Features**
- Multiple output formats (JSON, Markdown, CSV)
- In-process rate limiting across all tool calls
- Comprehensive logging with configurable levels (and an explicit truncation notice on capped results)

### MCP Capabilities Advertised

The server advertises these MCP capabilities:
- ✅ **Tools**: Full tool execution with comprehensive schemas
- ✅ **Resources**: Dynamic resource discovery, subscriptions, and templates
- ✅ **Prompts**: Enhanced prompts with resource integration
- ✅ **Notifications**: Real-time change notifications
- ✅ **Completion**: Argument completion for prompts and resource templates
- ✅ **Logging**: Client-controlled log level via `logging/setLevel`

## Supported MCP Functions

The server exposes the following MCP tools (see the [Tools](#tools) section above for full argument details):

**Database Operations:**
- **execute-snowflake-sql**: Executes a SQL query and returns results as JSON, markdown, or CSV
- **execute-query**: Query execution with read-only protection, row limit, and multiple output formats

**Discovery and Metadata:**
- **get-connection-info**: Current connection information and server status
- **list-snowflake-warehouses**: Lists available Data Warehouses with status
- **list-databases**: Lists all accessible databases, with optional pattern filtering
- **export-schema**: Exports hierarchical schema metadata in JSON, YAML, or SQL format (with optional capped table samples)

**Session Notes:**
- **add-note** / **delete-note** / **list-notes** / **get-note**: Manage in-memory notes for the session

The server also implements MCP **resources** (Snowflake objects with subscription support) and **prompts**. For parameter schemas, inspect `handle_list_tools` in `src/simple_snowflake_mcp/server.py`.

## 🚀 Getting Started Examples

### Basic Usage
```python
# Execute a simple query
{
  "name": "execute-query",
  "arguments": {
    "sql": "SELECT CURRENT_TIMESTAMP;",
    "format": "markdown"
  }
}

# List all databases
{
  "name": "list-databases",
  "arguments": {}
}
```

### Advanced Configuration
```yaml
# config_production.yaml
logging:
  level: WARNING
  file_logging:
    enabled: true
    filename: "logs/mcp_server.log"

snowflake:
  # Keep true unless the connecting Snowflake role is itself read-only.
  read_only: true
  default_query_limit: 5000
  max_query_limit: 100000
  statement_timeout_seconds: 120
  connection_reuse: true

security:
  rate_limit:
    enabled: true
    max_calls: 60
    window_seconds: 60
  notes:
    max_count: 100
    max_content_length: 10000

mcp:
  experimental_features:
    resource_subscriptions: true
```

### Debugging and Troubleshooting

**Enable Debug Logging:**
```bash
# Method 1: Environment variable
export LOG_LEVEL=DEBUG
python -m simple_snowflake_mcp

# Method 2: Custom config file
export CONFIG_FILE=config_debug.yaml
python -m simple_snowflake_mcp
```

**Common Issues:**
- **Connection errors**: Check your Snowflake credentials and network connectivity
- **Permission errors**: Ensure your user has appropriate Snowflake privileges
- **Query limits**: Adjust `default_query_limit` in config.yaml for large result sets
- **MCP compatibility**: Update to latest MCP client version for full feature support
