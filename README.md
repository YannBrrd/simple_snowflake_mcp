# Simple Snowflake MCP server

Simple Snowflake MCP Server to work behind a corporate proxy (because I could not get that in a few minutes with existing servers, but my own server, yup). Still don't know if it's good or not. But it's good enough for now.

<a href="https://glama.ai/mcp/servers/@YannBrrd/simple_snowflake_mcp">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/@YannBrrd/simple_snowflake_mcp/badge" alt="Simple Snowflake MCP server" />
</a>

### Tools

The server exposes the following MCP tools to interact with Snowflake:

- **execute-snowflake-sql**: Executes a SQL query on Snowflake and returns the result (list of dictionaries)
- **list-snowflake-warehouses**: Lists available Data Warehouses (DWH) on Snowflake
- **list-databases**: Lists all accessible Snowflake databases
- **list-views**: Lists all views in a database and schema
- **describe-view**: Gives details of a view (columns, SQL)
- **query-view**: Queries a view with an optional row limit (markdown result)
- **execute-query**: Executes a SQL query in read-only mode (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH) or not (if `read_only` is false), result in markdown format

## Quickstart

### Install

#### Claude Desktop

On MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`

On Windows: `%APPDATA%/Claude/claude_desktop_config.json`

<details>
  <summary>Development/Unpublished Servers Configuration</summary>


  ```
  "mcpServers": {
    "simple_snowflake_mcp": {
      "command": "uv",
      "args": [
        "--directory",
        ".", // Use current directory for GitHub
        "run",
        "simple_snowflake_mcp"
      ]
    }
  }
  ```
</details>

<details>
  <summary>Published Servers Configuration</summary>

  ```
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

- `SNOWFLAKE_USER`: Your Snowflake username (required)
- `SNOWFLAKE_PASSWORD`: Your Snowflake password (required)
- `SNOWFLAKE_ACCOUNT`: Your Snowflake account identifier (required)
- `SNOWFLAKE_WAREHOUSE`: Warehouse name (optional)
- `SNOWFLAKE_DATABASE`: Default database (optional)
- `SNOWFLAKE_SCHEMA`: Default schema (optional)
- `MCP_READ_ONLY`: Set to "TRUE" for read-only mode (default: TRUE)

#### Development Mode

For development, use the development profile which mounts your source code:

```bash
docker-compose --profile dev up simple-snowflake-mcp-dev -d
```

This allows you to make changes to the code without rebuilding the Docker image.

## Development

### Building and Publishing

To prepare the package for distribution:

1. Sync dependencies and update lockfile:
```bash
uv sync
```

2. Build package distributions:
```bash
uv build
```

This will create source and wheel distributions in the `dist/` directory.

3. Publish to PyPI:
```bash
uv publish
```

Note: You'll need to set PyPI credentials via environment variables or command flags:
- Token: `--token` or `UV_PUBLISH_TOKEN`
- Or username/password: `--username`/`UV_PUBLISH_USERNAME` and `--password`/`UV_PUBLISH_PASSWORD`

### Debugging

Since MCP servers run over stdio, debugging can be challenging. For the best debugging
experience, we strongly recommend using the [MCP Inspector](https://github.com/modelcontextprotocol/inspector).

You can launch the MCP Inspector via [`npm`](https://docs.npmjs.com/downloading-and-installing-node-js-and-npm) with this command:

```bash
npx @modelcontextprotocol/inspector uv --directory . run simple-snowflake-mcp
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
   git clone <your-repo>
   cd simple_snowflake_mcp
   python -m venv .venv
   .venv/Scripts/activate  # Windows
   pip install -r requirements.txt  # or `uv sync --dev --all-extras` if available
   ```

2. **Configure Snowflake access**
   - Copy `.env.example` to `.env` (or create `.env` at the root) and fill in your credentials:
     ```env
     SNOWFLAKE_USER=...
     SNOWFLAKE_PASSWORD=...
     SNOWFLAKE_ACCOUNT=...
     # SNOWFLAKE_WAREHOUSE   Optional: Snowflake warehouse name
     # SNOWFLAKE_DATABASE    Optional: default database name
     # SNOWFLAKE_SCHEMA      Optional: default schema name
     # MCP_READ_ONLY=true|false   Optional: true/false to force read-only mode
     ```

3. **Configure VS Code for MCP debugging**
   - The `.vscode/mcp.json` file is already present:
     ```json
     {
       "servers": {
         "simple-snowflake-mcp": {
           "type": "stdio",
           "command": ".venv/Scripts/python.exe",
           "args": ["-m", "simple_snowflake_mcp"]
         }
       }
     }
     ```
   - Open the command palette (Ctrl+Shift+P), type `MCP: Start Server` and select `simple-snowflake-mcp`.

4. **Usage**
   - The exposed MCP tools allow you to query Snowflake (list-databases, list-views, describe-view, query-view, execute-query, etc.).
   - For more examples, see the MCP protocol documentation: https://github.com/modelcontextprotocol/create-python-server

## Supported MCP Functions

The server exposes the following MCP tools to interact with Snowflake:

- **execute-snowflake-sql**: Executes a SQL query on Snowflake and returns the result (list of dictionaries)
- **list-snowflake-warehouses**: Lists available Data Warehouses (DWH) on Snowflake
- **list-databases**: Lists all accessible Snowflake databases
- **list-views**: Lists all views in a database and schema
- **describe-view**: Gives details of a view (columns, SQL)
- **query-view**: Queries a view with an optional row limit (markdown result)
- **execute-query**: Executes a SQL query in read-only mode (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH) or not (if `read_only` is false), result in markdown format

For each tool, see the Usage section or the MCP documentation for the call format.