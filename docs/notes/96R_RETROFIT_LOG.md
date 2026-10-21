# MedAgentPro — יומן ההשלמה של התיעוד והטיפול בשגיאות

**מקור:** כללי הקוד של אביתר (21.10, ‏21:25), ההנחיה של הצ׳אט מ-21:35, ו-`CLAUDE.md`, הסעיף "Code rules".
**מה זה:** הרשימה של מה שעוד חסר בקוד הישן. **כל קוד חדש או קוד שמשתנה** כבר עומד בכללים. התיעוד אחורה של הקוד הישן נעשה אחרי שהארכיטקטורה תושלם (הצ׳אט, 21:35), לפי התקן של 15:25 ו-`95_LEARNING_PLAN.md`.

## איך משתמשים ביומן

- **נגעת בקובץ ישן, ולא השלמת בו את התיעוד או את הטיפול בשגיאות?** מוסיפים שורה בסעיף 2: הקובץ, מה חסר, וה-commit.
- **השלמת קובץ?** מוחקים את השורה שלו בסעיף 1. אם הוא היה חסר בשגיאות, מוחקים גם את השורות שלו ב-`backend/tests/fixtures/code_rules_baseline.json`. הבדיקה (`tests/test_code_rules.py`) נכשלת כל עוד שורה של מקרה שתוקן נשארת שם, כך שהרשימה לא יכולה לטעון שנשאר יותר ממה שנשאר באמת.
- **מה נבדק אוטומטית:**
  - **raise של חריגה מובנית** (‏`ValueError`, ‏`RuntimeError`, …) ב-`backend/` מחוץ ל-`tests/`, ו-**`except` רחב שלא רושם ולא זורק:** נכשל ב-pytest, אם הוא לא ב-baseline.
  - **docstring חסר** (‏ruff D100–D104 ו-D106) בקבצים שה-PR משנה: אזהרה ב-CI, לא כשל.
- **מה לא נבדק אוטומטית, ונבדק בסקירה:** האיכות של התיעוד (מה, למה, איפה בצינור, קלט ופלט, כשל, הנחות והספר), וזה שההודעה למשתמש באה מטבלת הקבועים.

## 1. המצב ההתחלתי (21.10, על `98d8e4e`)

**הספירה של הצ׳אט (15:25, ‏master):** ‏239 מתוך 458 הגדרות ציבוריות עם docstring (52%), ו-13 מתוך 42 מודולים בלי docstring של מודול. הפונקציות הפרטיות לא נספרו.

**הספירה כאן, בכלים** (‏ruff D100–D104 ו-D106, והבדיקה של `test_code_rules`): ‏**41 קבצים.** ‏**191 הגדרות ציבוריות בלי docstring,** ‏**46 raise של חריגה מובנית,** ו-**`except` רחב אחד שלא רושם.** אין כאן את הפונקציות הפרטיות שיש בהן לוגיקה. הן נכללות בכלל, אבל אין כלי שסופר אותן.

| קובץ | docstrings חסרים | raise מובנה | except שקט | מה חסר |
|---|---|---|---|---|
| `persistence/audit_repository.py` | 28 | 3 | — | תיעוד ושגיאות |
| `query_assistance/prompts.py` | 9 | 13 | — | תיעוד ושגיאות |
| `shared/circuit_breaker.py` | 22 | — | — | תיעוד |
| `api/state.py` | 9 | 4 | — | תיעוד ושגיאות |
| `model_client/budget.py` | 11 | 2 | — | תיעוד ושגיאות |
| `tools/real_eval.py` | 11 | — | 1 | תיעוד ושגיאות |
| `query_assistance/compiler.py` | 6 | 4 | — | תיעוד ושגיאות |
| `query_assistance/synthesis.py` | 10 | — | — | תיעוד |
| `query_assistance/pipeline.py` | 3 | 6 | — | תיעוד ושגיאות |
| `tools/preflight.py` | 9 | — | — | תיעוד |
| `api/routes.py` | 4 | 3 | — | תיעוד ושגיאות |
| `model_client/client.py` | 6 | 1 | — | תיעוד ושגיאות |
| `model_client/replay.py` | 2 | 5 | — | תיעוד ושגיאות |
| `query_assistance/coverage.py` | 6 | 1 | — | תיעוד ושגיאות |
| `tools/dry_run.py` | 7 | — | — | תיעוד |
| `query_assistance/calls.py` | 5 | 1 | — | תיעוד ושגיאות |
| `query_assistance/contract.py` | 5 | 1 | — | תיעוד ושגיאות |
| `tools/bird_eval.py` | 5 | — | — | תיעוד |
| `tools/bird_fetch.py` | 3 | 2 | — | תיעוד ושגיאות |
| `demo_data/scripted.py` | 3 | — | — | תיעוד |
| `demo_data/seed.py` | 3 | — | — | תיעוד |
| `query_assistance/call_trace.py` | 3 | — | — | תיעוד |
| `tools/fake_real_server.py` | 2 | — | — | תיעוד |
| `tools/path_map.py` | 2 | — | — | תיעוד |
| `api/__init__.py` | 1 | — | — | תיעוד |
| `api/trace.py` | 1 | — | — | תיעוד |
| `demo_data/__init__.py` | 1 | — | — | תיעוד |
| `main.py` | 1 | — | — | תיעוד |
| `persistence/__init__.py` | 1 | — | — | תיעוד |
| `query_assistance/__init__.py` | 1 | — | — | תיעוד |
| `query_assistance/gate.py` | 1 | — | — | תיעוד |
| `reasoning/__init__.py` | 1 | — | — | תיעוד |
| `reasoning/router.py` | 1 | — | — | תיעוד |
| `shared/__init__.py` | 1 | — | — | תיעוד |
| `shared/auth.py` | 1 | — | — | תיעוד |
| `shared/guardrail_policy.py` | 1 | — | — | תיעוד |
| `shared/logger.py` | 1 | — | — | תיעוד |
| `tools/__init__.py` | 1 | — | — | תיעוד |
| `tools/book_tests.py` | 1 | — | — | תיעוד |
| `tools/file_reader.py` | 1 | — | — | תיעוד |
| `tools/mongo_queries.py` | 1 | — | — | תיעוד |

## 2. נגיעות בקוד ישן בלי השלמה

| תאריך | קובץ | מה חסר | commit | הערה |
|---|---|---|---|---|
| 21.10 | `.github/workflows/tests.yml` | — | (הענף `phaseb/code-rules`) | שלב חדש בלבד. לא קוד Python |
