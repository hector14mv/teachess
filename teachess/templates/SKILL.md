---
name: teachess
description: Read the user's own chess.com games through the teachess CLI (Stockfish-evaluated openings, repeated mistakes, losing streaks, monthly progress, spaced-repetition drills). Use when the user asks about their chess games, rating, openings, mistakes, progress, whether to keep playing tonight, or wants to be quizzed on their mistakes.
---

# teachess

`teachess` downloads the user's chess.com games, runs Stockfish over the first 12 moves of every rapid game of
the last year and keeps the result locally. Every command takes `--json`; always use it and read the JSON, never
the human output. Text fields (`advice`, `verdict`, `title`, `body`, `when`, opening names) are in Spanish: quote
them or translate them to the user's language.

## Commands

| command | returns |
|---|---|
| `teachess sync --json` | new games since the last sync (list), after fetching, evaluating and rebuilding the report. Run it first when the question is about recent games; it takes seconds, or minutes on the very first run |
| `teachess today --json` | the session right now: `state` (`streak`, `loss`, `win`, `draw`, `idle`, `empty`), `losses_in_a_row`, `advice`, `last` game, `score_after_loss` / `score_after_win` (% of points in games started within 45 min of a loss / a win) |
| `teachess progress --json [--months 6]` | `rating` (now, best), `months[]` (games, `score` %, end-of-month `rating`, `mistake_by12` = % of games with a serious mistake before move 12, lower is better; `median_first_mistake` = its move number, later is better), `year` baseline, `drills` counters |
| `teachess games --json [--since 30d\|2w\|3m\|YYYY-MM-DD] [--tc rapid\|blitz\|bullet\|daily\|all] [--limit 20]` | games, newest first: `result`, `side`, `opening`, `how`, `opp`, `opp_rating`, `my_rating`, `url` |
| `teachess openings --json [--color white\|black\|all]` | repertoire of the last year: `name`, `n` games, `score` %, `mistake_by12` % |
| `teachess review --json [last\|URL\|game id]` | one game: every move that lost ≥ 0.8 pawns (`mistakes[]` with `move`, `best_move`, `loss` in centipawns, `repeated` = how many earlier games had the same mistake) and a `verdict` |
| `teachess drill --json [--limit 10]` | flashcards due today: `stats` and `due[]`, each with `id`, `fen`, `line` (moves that led there), `ask`, `choices[]` (three moves: the best one, the one the user keeps playing, one more) |
| `teachess drill --answer CARD MOVE --json` | grades an answer (`MOVE` is a `choices[].san`), moves the card between Leitner boxes and returns `correct`, `best`, `title`, `body`, next `due` date |

A serious mistake is a move that loses 80 centipawns (0.8 pawns) or more. `score` is the share of points
(win 1, draw ½), not the win rate. Only rapid games are evaluated.

## How to use it well

- Ground every claim in a number you just read. Do not guess openings, ratings or mistakes.
- If `today.state` is `streak`, tell the user to stop for 45 minutes before anything else; the data says they
  score far less after a loss.
- To quiz: take one card from `drill --json`, show the position (the `line` and the `fen`, or a board drawn from
  the fen with the user's side at the bottom), offer the three `choices` without saying which is best, wait for
  the answer, then call `drill --answer` with the card `id` and the chosen `san` and explain the result.
- `drill --answer` changes the user's saved progress: only call it with a move the user actually chose.

## Exit codes

`0` ok · `1` unexpected error · `2` bad arguments · `3` setup missing (no username configured, no data yet:
run `teachess sync`) · `4` not found (game or card) · `5` chess.com unreachable or rate-limiting: wait, do not
retry in a loop. With `--json`, stderr carries `{"error": {"code", "exit", "message"}}`.
