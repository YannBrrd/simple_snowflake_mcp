# Simple Snowflake MCP server
[![Trust Score](https://archestra.ai/mcp-catalog/api/badge/quality/YannBrrd/simple_snowflake_mcp)](https://archestra.ai/mcp-catalog/yannbrrd__simple_snowflake_mcp)

**Enhanced Snowflake MCP Server with comprehensive configuration system and full MCP protocol compliance.**

A production-ready MCP server that provides seamless Snowflake integration with advanced features including configurable logging, resource subscriptions, and comprehensive error handling. Designed to work seamlessly behind corporate proxies.

### Tools

The server exposes comprehensive MCP tools to interact with Snowflake:

**Core Database Operations:**
- **execute-snowflake-sql**: Executes a SQL query on Snowflake and returns the result (list of dictionaries)
- **execute-query**: Executes a SQL query in read-only mode (SELECT, SHOW, DESCRIBE, EXPLAIN, WITH) or not (if `read_only` is false), result in markdown format
- **query-view**: Queries a view with an optional row limit (markdown result)

**Discovery and Metadata:**
- **list-snowflake-warehouses**: Lists available Data Warehouses (DWH) on Snowflake
- **list-databases**: Lists all accessible Snowflake databases
- **list-schemas**: Lists all schemas in a specified database
- **list-tables**: Lists all tables in a database and schema
- **list-views**: Lists all views in a database and schema
- **describe-table**: Gives details of a table (columns, types, constraints)
- **describe-view**: Gives details of a view (columns, SQL)

**Advanced Operations:**
- **get-table-sample**: Gets sample data from a table
- **explain-query**: Explains the execution plan of a SQL query
- **show-query-history**: Shows recent query history
- **get-warehouse-status**: Gets current warehouse status and usage
- **validate-sql**: Validates SQL syntax without execution

## 🆕 Configuration System (v0.2.0)

The server now includes a comprehensive YAML-based configuration system that allows you to customize all aspects of the server behavior.

### Configuration File Structure

Create a `config.yaml` file in your project root:

```yaml
# Logging Configuration
logging:
  level: INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  file_logging: false  # Set to true to enable file logging
  log_file: "mcp_server.log"  # Log file path (when file_logging is true)

# Server Configuration
server:
  name: "simple_snowflake_mcp"
  version: "0.2.0"
  description: "Enhanced Snowflake MCP Server with full protocol compliance"
  connection_timeout: 30
  read_only: true  # Set to false to allow write operations

# Snowflake Configuration
snowflake:
  read_only: true
  default_query_limit: 1000
  max_query_limit: 50000

# MCP Protocol Settings
mcp:
  experimental_features:
    resource_subscriptions: true  # Enable resource change notifications
    completion_support: false    # Set to true when MCP version supports it
  
  notifications:
    resources_changed: true
    tools_changed: true
  
  limits:
    max_prompt_length: 10000
    max_resource_size: 1048576  # 1MB
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

## 🚀 Installation Rapide

### Méthode 1 : Installation avec `uvx` (Recommandé)

```bash
# Installation et exécution directe
uvx simple-snowflake-mcp
```

### Méthode 2 : Installation depuis le code source

```bash
# Cloner le repo
git clone https://github.com/YannBrrd/simple_snowflake_mcp
cd simple_snowflake_mcp

# Installer avec uv (crée automatiquement un venv)
uv sync

# Exécuter
uv run simple-snowflake-mcp
```

### Méthode 3 : Développement

```bash
# Installer avec les dépendances de développement
uv sync --all-extras

# Lancer les tests
uv run pytest

# Linter avec ruff
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

## Développement

### Installation des dépendances

```bash
# Synchroniser toutes les dépendances (prod + dev)
uv sync --all-extras

# Mettre à jour les dépendances
uv lock --upgrade

# Ajouter une nouvelle dépendance
uv add <package-name>

# Ajouter une dépendance de dev
uv add --dev <package-name>
```

### Build et Publication

```bash
# Build
uv build

# Publier sur PyPI
uv publish --token $UV_PUBLISH_TOKEN
```

### Debugging avec MCP Inspector

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

## Installation et configuration dans VS Code

1. **Cloner le projet et installer les dépendances**
   ```sh
   git clone https://github.com/YannBrrd/simple_snowflake_mcp
   cd simple_snowflake_mcp
   
   # Installer avec uv (crée automatiquement un venv)
   uv sync --all-extras
   ```

2. **Configurer l'accès Snowflake**
   - Copier `.env.example` vers `.env` et remplir vos credentials :
     ```env
     SNOWFLAKE_USER=...
     SNOWFLAKE_PASSWORD=...
     SNOWFLAKE_ACCOUNT=...
     # SNOWFLAKE_WAREHOUSE   Optionnel: Nom du warehouse Snowflake
     # SNOWFLAKE_DATABASE    Optionnel: Nom de la base par défaut
     # SNOWFLAKE_SCHEMA      Optionnel: Nom du schéma par défaut
     # MCP_READ_ONLY=true|false   Optionnel: true/false pour forcer le mode lecture seule
     ```

3. **Configurer le serveur (v0.2.0)**
   - Le serveur créera automatiquement un fichier `config.yaml` par défaut au premier lancement
   - Personnalisez le logging, les limites et les fonctionnalités MCP en éditant `config.yaml`
   - Utilisez `CONFIG_FILE=custom_config.yaml` pour spécifier un fichier de configuration différent

4. **Configurer VS Code pour le debugging MCP**
   - Le fichier `.vscode/mcp.json` est déjà présent :
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
   - Ouvrir la palette de commandes (Ctrl+Shift+P), taper `MCP: Start Server` et sélectionner `simple-snowflake-mcp`.

5. **Utilisation**
   - Les outils MCP exposés vous permettent d'interroger Snowflake (list-databases, list-views, describe-view, query-view, execute-query, etc.).
   - Pour plus d'exemples, voir la documentation du protocole MCP : https://github.com/modelcontextprotocol/create-python-server

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
- Support for resource templates and prompts

**⚡ Performance & Reliability**
- Configurable query limits and timeouts
- Comprehensive error handling with detailed error codes
- Connection pooling and retry mechanisms

**🔧 Development Features**
- Multiple output formats (JSON, Markdown, CSV)
- SQL syntax validation without execution
- Query execution plan analysis
- Comprehensive logging with configurable levels

### MCP Capabilities Advertised

The server advertises these MCP capabilities:
- ✅ **Tools**: Full tool execution with comprehensive schemas
- ✅ **Resources**: Dynamic resource discovery and subscriptions  
- ✅ **Prompts**: Enhanced prompts with resource integration
- ✅ **Notifications**: Real-time change notifications
- 🚧 **Completion**: Ready for future MCP versions (configurable)

## Supported MCP Functions

The server exposes comprehensive MCP tools to interact with Snowflake:

**Core Database Operations:**
- **execute-snowflake-sql**: Executes a SQL query and returns structured results
- **execute-query**: Advanced query execution with multiple output formats
- **query-view**: Optimized view querying with result limiting
- **validate-sql**: SQL syntax validation without execution

**Discovery and Metadata:**
- **list-snowflake-warehouses**: Lists available Data Warehouses with status
- **list-databases**: Lists all accessible databases with metadata  
- **list-schemas**: Lists all schemas in a specified database
- **list-tables**: Lists all tables with column information
- **list-views**: Lists all views with definitions
- **describe-table**: Detailed table schema and constraints
- **describe-view**: View definition and column details

**Advanced Analytics:**
- **get-table-sample**: Sample data extraction with configurable limits
- **explain-query**: Query execution plan analysis
- **show-query-history**: Recent query history with performance metrics
- **get-warehouse-status**: Real-time warehouse status and usage
- **get-account-usage**: Account-level usage statistics

For detailed usage examples and parameter schemas, see the MCP protocol documentation.

## 🚀 Getting Started Examples

### Basic Usage
```python
# Execute a simple query
{
  "name": "execute-query",
  "arguments": {
    "query": "SELECT CURRENT_TIMESTAMP;",
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
  file_logging: true
  log_file: "/var/log/mcp_server.log"

server:
  read_only: false  # Allow write operations
  
snowflake:
  default_query_limit: 5000
  max_query_limit: 100000

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
