#!/bin/sh
# Ollama.app relaunches its own server at login and grabs :11434 first.
# Evict it and take the port, so no manual Login Items toggle is needed.
# -x matches the process name exactly; `pkill -f` would match any command
# line merely mentioning the path, including the shell that spawned us.
killall Ollama 2>/dev/null   # the GUI app  (capital O)
pkill -x ollama 2>/dev/null  # its server   (lowercase)
sleep 1
exec /Applications/Ollama.app/Contents/Resources/ollama serve
