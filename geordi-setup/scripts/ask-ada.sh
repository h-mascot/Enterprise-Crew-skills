#!/bin/bash
# Geordi → Ada: Ask a question via #geordi-bridge
# Usage: ask-ada.sh "question"

WEBHOOK_URL="https://discord.com/api/webhooks/1474651838757343242/O9byiAS2OWjT-UdL49FyQkT61apQC89gdfEAbbaklFmMcDKTiyN-JDzYQGYTQTqcv2rL"

QUESTION="${1:?Usage: ask-ada.sh \"question\"}"

CONTENT=$(printf "👷 **Geordi → Ada** 🔮\n\n❓ %s" "$QUESTION")

curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg content "$CONTENT" '{username: "Geordi 👷", content: $content}')" > /dev/null

echo "✅ Question posted to #geordi-bridge"
