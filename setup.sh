#!/bin/bash
set -e

echo "🚀 Configuration de simple-snowflake-mcp avec uv..."

# Vérifier si uv est installé
if ! command -v uv &> /dev/null; then
    echo "📦 Installation de uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "✅ uv installé"
fi

# Créer .env si nécessaire
if [ ! -f .env ]; then
    echo "📝 Création du fichier .env..."
    cp .env.example .env
    echo "⚠️  Merci d'éditer .env avec vos credentials Snowflake"
fi

# Installer les dépendances
echo "📦 Installation des dépendances..."
uv sync

echo ""
echo "✅ Configuration terminée !"
echo ""
echo "Commandes disponibles :"
echo "  uv run simple-snowflake-mcp    - Lancer le serveur"
echo "  uv run pytest                   - Lancer les tests"
echo "  uv sync --all-extras            - Installer les dépendances de dev"
echo "  make help                       - Voir toutes les commandes"
