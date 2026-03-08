#!/bin/bash
# Geordi → #geordi-bridge: Post build report
# Usage: report.sh "summary" ["details"]

WEBHOOK_URL="https://discord.com/api/webhooks/1474651838757343242/O9byiAS2OWjT-UdL49FyQkT61apQC89gdfEAbbaklFmMcDKTiyN-JDzYQGYTQTqcv2rL"

SUMMARY="${1:?Usage: report.sh \"summary\" [\"details\"]}"
DETAILS="${2:-}"

if [ -n "$DETAILS" ]; then
  CONTENT=$(printf "👷 **Geordi Report**\n\n**%s**\n\n%s" "$SUMMARY" "$DETAILS")
else
  CONTENT=$(printf "👷 **Geordi Report**\n\n%s" "$SUMMARY")
fi

# Truncate to Discord's 2000 char limit
CONTENT="${CONTENT:0:1990}"

curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg content "$CONTENT" '{username: "Geordi 👷", content: $content}')" > /dev/null

echo "✅ Report posted to #geordi-bridge"
