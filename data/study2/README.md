# Study 2 (Prolific, September 2026) — revalidation of I2 / C3 / S1

Between-subjects replication of Study 1 for the three replaced animations
(I2 Nametag → Silhouette, C3 Ghost Outline → Missing Piece, S1 Reveal → Peek),
same protocol, same scenes, same narrator texts and options; only the videos
and the correction `pipeline_intent` change. 40 participants, 4 lists of 10,
~5 minutes: block 1 = 12 forced-choice items (9 targets + 3 fillers), block 2 =
3 Likert items. Spec: `~/Downloads/study2_prolific_spec.md`.

## Files

| File | Built by | Content |
|---|---|---|
| `study2_lists.json` | `python3 -m scripts.study2.build_lists --verify` | 4 lists: block 1 (12) and block 2 (3) per list, slot→list mapping, condition rule |
| `study2_all_stimuli.json` | `python3 -m scripts.study2.build_stimuli` | 24 targets (`study2_*`, from the Study 1 entries) + 24 fillers (`study1_{P2f,C2,T1}_*`, verbatim) |
| `study2_intents.json` | hand-written | the 12 rewritten correction intents |
| `study2_config.json` | hand-written | survey configuration loaded by `survey/index.html?study=2` |
| `../study2_videos/` | `video_ui` batch + copies | `study2_{I2,C3,S1}_{A-D}.mp4` (new visuals) and the 12 filler videos copied byte-for-byte from `data/prolific_videos/` |
| `private/study1_prolific_ids.txt` | from the Study 1 `state` sheet | 82 Prolific IDs to exclude (git-ignored) |
| `analysis/` | `scripts/analysis/prolific_study.py` | CSV outputs (git-ignored) |

Condition rule: the spec's parity rule gives 8/4 per animation in block 1; the
rule implemented in `build_lists.py` gives 6/6 per animation and every one of
the 24 target stimuli is seen by exactly 2 lists (20 participants) across both
blocks. `tests/test_study2_lists.py` checks it.

## Recording the 12 target videos

```
python3 -m scripts.study_gen.video_ui
```

Open `http://127.0.0.1:5557/?only=I2,C3,S1&prefix=study2_&save=study2_videos` in Chrome, check the
three animations on one scene each (S1 B and C halo the hidden entity declared
as `peek_hidden` in the scene JSON), then **Batch** → 12 downloads named
`study2_<anim>_<scene>.mp4` written directly to `data/study2_videos/` (without `save=`, the browser downloads them instead).

The batch stalls in a hidden/background tab (requestAnimationFrame is
throttled): keep the tab visible, or record headlessly with
`scripts/study2/record_videos.js` (Playwright, see its header). The 12 videos
in `data/study2_videos/` were recorded that way (3.5-3.7 s each, like Study 1).

## Deployment

1. New Google Sheets spreadsheet with sheets `state`, `responses_block1`,
   `responses_block2` and the headers listed at the top of `survey/apps_script.js`.
2. Extensions → Apps Script, paste `survey/apps_script.js`, run
   `setupStateSheet(40, 10)` once, deploy as web app (execute as me, anyone).
3. Paste the deployment URL into `study2_config.json` → `api_base`, and the
   Prolific completion code into `completion_code`.
4. Commit and push (`data/study2/`, `data/study2_videos/`, `survey/`); GitHub
   Pages serves `https://vincentcavez.github.io/Tellimation/survey/?study=2`.
5. Dry run: `?study=2&demo=1` (no API calls, list 1).

## Prolific

- Study URL: `https://vincentcavez.github.io/Tellimation/survey/?study=2&PROLIFIC_PID={{%PROLIFIC_PID%}}`
- 40 places. Filters as in Study 1: first language English, approval rate 100 %,
  no colour-blindness, desktop only.
- Exclusion: custom blocklist = `private/study1_prolific_ids.txt`.

## Data export

Export each response sheet with **File → Download → Comma-separated values**
(never via Excel: some Study 1 block 2 exports came out with `;`). Then:

```
python3 -m scripts.analysis.prolific_study \
  --s1-block1 "~/Downloads/Prolific data - responses_block1.csv" \
  --s1-block2 "~/Downloads/Prolific data - responses_block2.csv" \
  --s2-block1 study2_block1.csv --s2-block2 study2_block2.csv
```
