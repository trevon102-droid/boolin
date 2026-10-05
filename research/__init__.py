"""Boolin research layer.

Reads the raw source files the pipeline writes (data/latest/*.json) and builds DERIVED research
objects in data/research/: game research cards, market history (open / current / close),
a "what changed" timeline, research flags, a daily research board, scenarios, comparable-game
context, analyst notebooks and postgame grades.

Raw data (data/latest, data/days) is never modified here. Everything this package writes is
derived and says so (`kind: "derived"`), with the inputs it came from.
"""
SCHEMA_VERSION = "research/1"
