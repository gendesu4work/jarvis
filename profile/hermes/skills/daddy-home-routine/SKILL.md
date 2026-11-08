---
name: daddy-home-routine
version: 1.2.0
description: "Use when user says daddys home to open Spotify."
author: Jarvis
license: MIT
metadata:
  hermes:
    tags: [routine, shortcut]
    related_skills: []
---
# Routine: "daddys home"
## When to Use
Use when the user says "daddys home" to open Spotify and play the requested media.

## Behavior:
- When the user says "daddy's home" or "that is home", respond with "Daddy's home.", open Spotify using `open-app "Spotify"`, open `https://open.spotify.com/playlist/5AXBZqcu0HJeWRi6lFNm8Y?si=5b06137c090e46fc`, and run `spotify play`.

## Procedure:
1. Trigger appropriate response ("Welcome back, sir." or "Daddy's home.").
2. Run `open-app "Spotify" && spotify play`.