# TwinMind Manual Evaluation Template

Use this template to evaluate each exported case from `eval/out/twinmind_eval_packet.md`.

## 1) Overall Weighted Score (Per Case)

Use these weights (higher priority gets higher weight):

1. Quality of live suggestions (`30%`)
2. Quality of detailed chat answers when clicked (`20%`)
3. Prompt engineering quality (`15%`)
4. Full-stack engineering quality (`10%`)
5. Code quality (`10%`)
6. Latency (`10%`)
7. Overall experience (`5%`)

Score each category 1-5:
- 1 = poor
- 2 = weak
- 3 = acceptable
- 4 = strong
- 5 = excellent

Weighted score formula (0-100):
`weighted_score = sum(category_score * category_weight) * 20`

## 2) Per-Case Scoring Sheet

Copy for each case:

```text
Case ID:
Source File:

1) Live Suggestions (30%) — score 1..5:
- Useful?
- Well-timed to recent context?
- Varied by context (Answer / Question to Ask / Talking Point / Fact Check)?
- Preview has standalone value even if not clicked?
Notes:

2) Detailed Chat Answer (20%) — score 1..5:
- Correct and useful?
- Specific, actionable, grounded in transcript?
- Clear structure and practical next step?
Notes:

3) Prompt Engineering (15%) — score 1..5:
- Right context volume passed?
- Context structured well?
- Good decisions on suggestion type/mix?
- Robust behavior across meeting styles?
Notes:

4) Full-Stack Engineering (10%) — score 1..5:
- UI polish and interaction quality?
- Backend structure and API behavior?
- Audio capture/chunking reliability?
- Error handling quality?
Notes:

5) Code Quality (10%) — score 1..5:
- Clean structure and readability?
- Sensible abstractions?
- Minimal dead code?
- Useful documentation?
Notes:

6) Latency (10%) — score 1..5:
- Reload click -> suggestions rendered
- Chat sent -> first token
Notes:

7) Overall Experience (5%) — score 1..5:
- Feels responsive and trustworthy in real conversation?
Notes:

Final Weighted Score (0-100):
Verdict (ship / improve / block):
Top 3 improvements:
```

## 3) Session-Level Summary

After scoring all cases, summarize:

1. Mean weighted score
2. p25 / median / p75 weighted score
3. Biggest recurring failures (top 3)
4. Prompt changes to test next (A/B)
5. Engineering fixes to prioritize next sprint

