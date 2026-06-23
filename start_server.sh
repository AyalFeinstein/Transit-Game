#!/bin/bash
# Quick start script for TransitGame server

echo "Starting TransitGame Server..."
echo "Server will run on port 8080 (or specify port as argument)"
echo "Press Ctrl+C to stop the server"
echo ""

PORT=${1:-8080}
python server.py $PORT

