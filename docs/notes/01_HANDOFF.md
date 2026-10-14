# מסירה לשיחה חדשה (14.10, צהריים)

## לשיחת הצ׳אט (פרויקט J&J)

**תפקיד:** מחקר, החלטות ארכיטקטורה, הספר, וסקירת העבודה של Code. **לא לאשר את מה ש-Code עשה — לחקור ולבדוק בעצמנו.** Code כותב קוד בריפו `C:\Users\EvyatarS\MedAgentPro`, ענף `master` (משימה חדשה — ענף חדש מ-`master` ו-PR אליו, merge commit בלבד). **הצ׳אט לא מריץ ולא בונה קוד בריפו** — כותב הנחיות ל-Code ב-`docs\notes\00_STATUS.md`, וגם השאלות של Code נרשמות שם.

**כללים קבועים:** תשובות בעברית בלבד; קוד, הערות ו-commits באנגלית · לא למחוק מ-`project filles\` · **לא לגעת בספר** — לומר מה לשנות ואיפה · מפתח, מודל, מחירים ותקרות רק ב-`backend\.env`, שאביתר כותב · תקציב מודל כולל עד $10 · **אין מפתח API ואין קריאה למודל אמיתי עד שמשימות 6–9 גמורות** (בדיקת העשן R9 בסוף, לפני הדמו) · לא לעשות קומפקטינג — להתריע מראש ולפתוח שיחה חדשה.

**מה לקרוא קודם:** `CLAUDE.md` (במיוחד "Path map and feasibility", "Task 5 review", "Decisions in force") · `docs\notes\00_STATUS.md` מהסעיף "משימה 9 — תוכנית של Code" ועד הסוף (ולפי הצורך — סעיפים קודמים) · `docs\notes\60_PATHS.md` (נוצר אוטומטית) · `docs\notes\40_CHAT.md` לפי הצורך.

**המצב:**
- **משימות 0–8 ב-`master`** (PR #1–#3 מוזגו, merge commits). צד השרת, מסך הצ׳אט (Next.js, איור 15) וסט 12 שאלות הדמו. **המסמך למציג:** `docs\notes\70_DEMO.md`
- **משימה 9 — בדיקות מקצה לקצה: מוזגה** (PR #4, ראו למטה). מה נכנס:
  - מבחן 38: כל הסט לשני התפקידים (Approver ו-PL-02), על Chromium, Firefox ו-WebKit ב-CI (במחשב Edge בלבד)
  - מבחן 40: מקלדת בלבד, ניגודיות (axe, 4 כשלים תוקנו), גווני אפור (בדיקה מבנית + 9 צילומים ב-`docs\notes\task9_grayscale\`)
  - מבחן 39 ממתין לסביבת החברה (בכוונה)
  - מצב המודל האמיתי מקצה לקצה עם ספק מזויף (`tools\fake_real_server.py`) — עלות, תקציב, תקרת שאלה
  - `python -m tools.preflight` — בדיקה לפני הדמו, הצעד הראשון ב-`70_DEMO.md`
  - טבלת מבחני הספר ← בדיקות ב-`60_PATHS.md`: כל 15 מבחני הצ׳אט והמסך מכוסים חוץ מ-39
  - **אילוץ חדש L05:** השאלה הגדולה בסט שומרת ~0.13$ לנתיב הגרוע; התקרה 0.25$ מעבירה את כל 12. תקרה של 0.12$ הייתה עוצרת גם את שאלה 1
  - **הריצה במחשב:** 684 בדיקות שרת (כיסוי 96.0%), stress 8, ‏path_map 132, Vitest 64, Playwright 12 (גרסה בנויה) + 3 (מצב אמיתי מזויף). ruff, bandit, pip-audit, ‏npm audit נקיים
- **משימה 9 מוזגה (14.10):** PR #4, ‏merge commit ‏`794dc24`.
  - **ה-CI הראשון נכשל ב-3 בדיקות:**
    - שתיים ב-Firefox ואחת ב-WebKit. **שתיים מהן היו בידוד בין מנועים** — שרת אחד משרת את כולם.
    - **השלישית הייתה המקלדת ב-Firefox.** החיפוש בשני הכיוונים פתר אותה.
  - **תוקן ב-`8756e66`.** אחרי זה #19–#22 ירוקים בשלושת המנועים.
  - **Firefox חסום במחשב על ידי Application Control,** ונבדק רק ב-CI. WebKit רץ במחשב (`E2E_ENGINES=webkit`).
- **ענפים:** ב-GitHub נשארו רק `main` ו-`master`, ובמחשב רק `master`. ארבעת ענפי המשימות נמחקו אחרי שווידאנו שהכול ב-`master`.
- **`main` נשאר ענף ברירת המחדל**, כי לאביתר אין הרשאת admin בריפו של מתן. מה זה אומר:
  - **כל PR נפתח מול `master` במפורש:** `https://github.com/MatanAmran/MedAgentPRO/compare/master...<branch>?expand=1`. GitHub מציע את `main`, אז לפני יצירת ה-PR לבדוק שכתוב "base: master".
  - **בדף הראשי של הריפו מוצג `main`** (רק README). זה לא אומר שהעבודה לא מוזגה.
  - **`stress` רצה על כל PR מול `master`,** וחוסמת כמו שאר העבודות (כ-45 שניות במחשב). `schedule` ו-`workflow_dispatch` רצים רק כשה-workflow בענף ברירת המחדל, וב-`main` יש רק README, ולכן אין הרצה יומית ואין כפתור "Run workflow" (תחזוקת CI, 14.10).
  - **אחרי כל מיזוג:** למחוק את ענף המשימה ב-GitHub (`git push MedAgentPRO --delete <branch>`, אחרי `git log master..MedAgentPRO/<branch>` ריק) ובמחשב (`git branch -d`).
- **CI** רץ ב-GitHub על כל push ו-PR, בעבודות `tests`, ‏`frontend` ו-`e2e`. ב-`e2e` רצים שלושה מנועים, ואחריהם המצב האמיתי עם הספק המזויף.
- **hook לפני commit** (ruff ו-bandit) — לא ב-git. ההתקנה ב-`CLAUDE.md`.

**הבא בצ׳אט:**
1. **תחזוקת CI, לפני הדמו** (סעיפים 2–4 של "סקירה בצ׳אט (14.10, 13:30)" ב-`00_STATUS.md`):
   - **Ubuntu:** נעילת `ubuntu-24.04`, כי `ubuntu-latest` עובר ל-26 ב-19.10.
   - **ה-actions:** גרסאות ל-Node 24.
   - **mypy:** ספירה של השגיאות בלבד, וההחלטה בצ׳אט.
   - **Code מציג תוכנית לפני קוד,** בענף חדש מ-`master`.
2. **לאביתר — בדיקה בעין:** 9 הצילומים בגווני אפור — כל מצב מזוהה בלי צבע
3. **בדיקת העשן מול המודל האמיתי (R9):** מפתח API נפרד (`medagentpro-demo`), ‏`backend\.env` שאביתר ממלא (`MODEL_ID=claude-sonnet-5-5`, בלי `MODEL_TEMPERATURE`, תקרה כוללת מתחת לקרדיט), ‏`MEDAGENTPRO_CHAIN_KEY`, ‏`python -m tools.preflight --real`, ואז שאלה אחת באישור אביתר. בודקים: הספק מקבל את 3 הסכמות (V01), בלי temperature (V02), העלות שנרשמה = Usage ב-Console (V03), שיעור ניסיונות המשוב (V04). **אחריה מחליטים:** דמו על המודל האמיתי או על המתוסרט
4. **חזרה על הדמו** לפי `70_DEMO.md`
5. **לאביתר לספר:** בקשת פעולה (הרחבה של FR 14) — שלושה שינויים מדויקים: משפט בסוף פסקת השער ב-4.9 ("…receives an offer to open an investigation instead." ← להוסיף: "A request to change records rather than read them retrieves nothing and receives a fixed statement that the assistant does not change records."), משפט שני ב-FR 14 ("Where a question asks to change records rather than read them, the system shall retrieve nothing and state that it does not change records."), וצעד רביעי במבחן 22 ("Pose a request to change a value held in the records, and read what is returned." / תוצאה: "The third changes nothing and retrieves nothing. What returns states that the assistant does not change records."). ובנוסף: גרש מעוגל `user’s` ב-4.9, ורענון תוכן העניינים

**שאלות פתוחות ללקוח:** האם להעתיק ליומן את השורות שהוצגו (K2) · גישה מלאה של מתכנן ל-`receipts` ול-`parts`.

## לשיחת Code החדשה

`CLAUDE.md` נטען אוטומטית.

**איפה העבודה עומדת (14.10, סוף השיחה):**
- **הענף:** `ci/maintenance`, מ-`master` (`794dc24`, המיזוג של PR #4).
- **ה-commits** — חמישה, ‏**כולם מקומיים. הענף לא עלה ל-GitHub, ואין לו PR:**
  - `8a8b112`, ‏`502792b` — הערות: מיזוג PR #4, ניקוי הענפים, ו-`main` כברירת מחדל.
  - `50d7ad8` — הערות: התוכנית לתחזוקת ה-CI, וספירת mypy.
  - `3730a91` — **הקוד:** ‏`tests.yml`, ‏`CLAUDE.md` ו-`01_HANDOFF.md`.
  - ה-commit של העדכון הזה.
- **`git status` נקי** אחרי ה-commit האחרון.

**מתוך "תחזוקת CI — הכרעות בצ׳אט (14.10, 14:10)":**
- **בוצע (`3730a91`):**
  1. `runs-on: ubuntu-24.04` בארבע העבודות, עם הערה.
  2. ה-actions נשארו כמו שהם. השדרוג ל-`@v7` נרשם ב-`CLAUDE.md`, תחת "After the demo".
  3. `stress` רצה על `pull_request` (‏`schedule` ו-`workflow_dispatch` נשארו). תוקן ב-`CLAUDE.md` וב-`01_HANDOFF.md`. **הזמן במחשב: כ-45 שניות.**
  4. ההערה של mypy: "does not block until after the demo". **10 השגיאות (`union-attr`, ‏`operator`) נקראו מול הקוד** ונרשמו ב-`00_STATUS.md`: אף אחת לא מגיעה ל-`None` בזמן ריצה בדמו.
- **נשאר — סעיף 5:**
  - **push של `ci/maintenance`, באישור מפורש של אביתר** (עוד לא ניתן לי).
  - **PR מול `master`:** ‏`https://github.com/MatanAmran/MedAgentPRO/compare/master...ci/maintenance?expand=1`, ולבדוק שכתוב "base: master".
  - **לאמת ב-CI:** ש-`stress` רצה ועברה על 24.04. **זו הפעם הראשונה שהיא רצה ב-CI אי פעם.** וגם שה-artifact של צילומי גווני האפור נוצר.
  - **אחרי המיזוג:** למחוק את הענף ב-GitHub ובמחשב, לפי `CLAUDE.md`.

**שאלה פתוחה לצ׳אט (לא חוסמת):** שגיאת mypy מס׳ 6 ב-`budget.py:186` (‏`SpendLedger.add`). אם `.model_spend.json` נערך ידנית עם מחרוזת, הרישום נכשל אחרי קריאה ששולמה. בדמו זה לא קורה. **תיקון של שורה אחת** — ‏`float(d["total_usd"]) + usd`, כמו `total` — מחכה להכרעה: עכשיו או אחרי הדמו.

**הוכרע בצ׳אט (14.10, 14:15):** לתקן את שגיאה 6 עכשיו, אבל באימות בטעינה ולא ב-`float()` בתוך `add`. הפירוט ב-`00_STATUS.md`, "סקירה בצ׳אט (14.10, 14:15)". **זה הצעד הבא, לפני ה-push.**

**בוצע (14.10, ‏`002529f`):** האימות בטעינה, ועוד `NaN` ושלילי (הצעה שלי, לבדיקת הצ׳אט). 11 בדיקות חדשות, ‏695 בסך הכול. הפירוט ב-`00_STATUS.md`, "שגיאה 6 תוקנה". **הענף: שמונה commits מקומיים.** הבא: push באישור אביתר, ואז PR מול `master`.

**משפט פתיחה:**

> קרא את `docs/notes/01_HANDOFF.md` (במיוחד "איפה העבודה עומדת") ואת הסעיף האחרון ב-`docs/notes/00_STATUS.md`. בדוק ב-`git log` וב-`git status` שהענף `ci/maintenance` עומד כמו שכתוב. ההמשך הוא push באישור אביתר, ואחריו PR מול `master`. בלי push בלי אישור אביתר.
