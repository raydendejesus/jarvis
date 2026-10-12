"""
How long each local model stays loaded in memory after its last use.

Holding a model resident forever makes every reply instant but pins its
memory for as long as Echo runs - the main cost on a smaller machine. Letting
an idle model unload frees that memory; the price is a slower FIRST reply
after a quiet spell, while every reply after it is as fast as before. Phone
calls never feel it: the brain is loaded before a call is placed and as one is
answered, and the opening line is already rendered.
"""

BRAIN_KEEP_ALIVE = "20m"
VISION_KEEP_ALIVE = "5m"
