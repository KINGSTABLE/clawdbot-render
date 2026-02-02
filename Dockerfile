# Use Node.js 22 as required by the latest OpenClaw/Clawdbot
FROM node:22-slim

# Set working directory
WORKDIR /app

# Install system dependencies
# We include 'gettext-base' to get the 'envsubst' tool for config injection
RUN apt-get update && apt-get install -y \
    git \
    python3 \
    build-essential \
    ca-certificates \
    iproute2 \
    gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Install pnpm globally
RUN npm install -g pnpm

# Clone the Clawdbot/OpenClaw repository
RUN git clone https://github.com/clawdbot/clawdbot.git .

# Install dependencies
RUN pnpm install

# Build the project
RUN pnpm run build

# Change ownership to the 'node' user
RUN chown -R node:node /app

# Create the startup script
# This script dynamically creates the configuration file using Render's PORT
RUN echo '#!/bin/bash\n\
\n\
# Render provides a dynamic PORT variable (usually 10000)\n\
# We default to 3000 if it is missing\n\
export APP_PORT=${PORT:-3000}\n\
export GATEWAY_BIND=0.0.0.0\n\
\n\
# Verify Secrets\n\
if [ -z "$OPENAI_API_KEY" ]; then\n\
    echo "Error: OPENAI_API_KEY is missing."\n\
    exit 1\n\
fi\n\
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then\n\
    echo "Error: TELEGRAM_BOT_TOKEN is missing."\n\
    exit 1\n\
fi\n\
\n\
# --- MANUAL CONFIG GENERATION ---\n\
echo "Creating manual configuration for Port $APP_PORT..."\n\
mkdir -p /home/node/.openclaw\n\
\n\
# We generate openclaw.json using the NEW schema (models instead of llm)\n\
# We inject the Render PORT dynamically\n\
cat <<EOF > /home/node/.openclaw/openclaw.json\n\
{\n\
  "gateway": {\n\
    "mode": "local",\n\
    "port": $APP_PORT\n\
  },\n\
  "channels": {\n\
    "telegram": {\n\
      "enabled": true,\n\
      "botToken": "$TELEGRAM_BOT_TOKEN",\n\
      "dmPolicy": "pairing"\n\
    }\n\
  },\n\
  "models": {\n\
    "providers": {\n\
      "openai": {\n\
        "apiKey": "$OPENAI_API_KEY"\n\
      }\n\
    },\n\
    "primary": "openai/gpt-4o"\n\
  }\n\
}\n\
EOF\n\
\n\
# Fix permissions so the "node" user can read the config\n\
chown -R node:node /home/node/.openclaw\n\
\n\
echo "--- Configuration Created. Starting Gateway on port $APP_PORT ---"\n\
\n\
# Start the gateway directly\n\
pnpm start -- gateway\n\
' > /app/start.sh

# Make script executable and fix ownership
RUN chmod +x /app/start.sh && chown node:node /app/start.sh

# Switch to non-root user
USER node

# Start the container
CMD ["/app/start.sh"]
