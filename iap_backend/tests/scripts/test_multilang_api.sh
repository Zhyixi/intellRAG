#!/bin/bash

URL="http://127.0.0.1:44000/api/v1/iap_ae/retrieve"

SESSION_ID="b0ee604b-08e9-4241-94ec-2f5e70f627e0"
ROLE="10038437"

LANGS=("EN" "VI" "PT" "ES" "ZH")

CONTENTS=(
"What should I do if the motor brake is abnormal?"
"Tôi nên làm gì nếu phanh động cơ bất thường?"
"O que devo fazer se o freio do motor estiver anormal?"
"¿Qué debo hacer si el freno del motor es anormal?"
"如果馬達煞車異常，我該怎麼辦？"
)

for i in "${!LANGS[@]}"
do
    LANG=${LANGS[$i]}
    CONTENT=${CONTENTS[$i]}

    echo "===================================="
    echo "Testing language: $LANG"
    echo "Question: $CONTENT"
    echo "===================================="

    curl -s -X POST "$URL" \
    -H "accept: application/json" \
    -H "Content-Type: application/json" \
    -d "{
        \"role\": \"$ROLE\",
        \"content\": \"$CONTENT\",
        \"session_id\": \"$SESSION_ID\",
        \"syslang\": \"$LANG\",
        \"test_flag\": false
    }"

    echo ""
    echo ""
done