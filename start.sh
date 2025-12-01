#!/bin/bash

# Start both processes in background
gunicorn dashboard:app --bind 0.0.0.0:5000 --timeout 120 &
python PriceChangeScanner.py &

# Wait for any process to exit
wait

# Exit with status of process that exited first
exit $?
