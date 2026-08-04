"""Arrival — the first path by which a row can enter this system at runtime.

Before this package there was exactly one way for a transaction to exist:
regenerate `fixtures/synthetic_data.sql` and rebuild the database. The engine
detected fraud correctly, but only fraud that was already in the file it was
handed, so every demo was a REBUILD rather than a CATCH.

Three doors, and they mean different things:

  * `authorize`  — an authorization REQUEST. The engine decides before the row
    is committed and the row is written carrying the outcome the engine chose.
    A charge the engine declines is never an approved transaction, which is
    what makes prevention real here rather than retrospective.
  * `arrivals`   — settled rows that already happened: transactions the
    processor already ruled on, behavioural events, link edges. The engine
    evaluates them afterwards, on the next cycle.
  * `watermark`  — how far the background cycle has consumed, in EVENT time.

`records` is shared by all three and by `engine/simulate.py`, which is where
the validation was first written: session 4 fabricates a transaction inside a
rollback and needed exactly the same reference checks, column allow-list and
defaults. One definition of "a row this system will accept", used by the path
that pretends and the path that commits — the only difference between them
being the COMMIT, which is the same argument WEEK5-PLAN decision 6 makes about
simulating and publishing a rule.
"""
