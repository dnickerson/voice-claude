#!/bin/bash
while true; do
    clear
    echo "=== Voice Claude — tmux sessions ==="
    echo ""
    mapfile -t sessions < <(tmux ls -F '#S' 2>/dev/null)
    if [ ${#sessions[@]} -eq 0 ]; then
        echo "  No tmux sessions running."
        echo ""
        read -p "Press Enter to refresh... " _
        continue
    fi
    for i in "${!sessions[@]}"; do
        printf "  %d. %s\n" "$((i+1))" "${sessions[$i]}"
    done
    echo ""
    read -p "Connect to [1-${#sessions[@]}]: " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#sessions[@]} )); then
        tmux attach-session -t "${sessions[$((choice-1))]}"
    fi
done
