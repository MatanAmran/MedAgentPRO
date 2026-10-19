# MedAgentPro — כלליות: תוכן תחום בקוד (משימה 11a, ‏19.10)

**דוח בלבד. שום קוד לא שונה.** הסריקה עברה על `backend/query_assistance/` (כולל `templates/*.md` ו-`medagentpro_schemas.json`), ‏`backend/api/` ו-`frontend/app`, ‏`frontend/lib`, מחוץ ל-`demo_data/`. חיפשתי שמות טבלאות ועמודות, מונחי רכש (supplier, part, purchase order, commitment, late, delivery, reorder), ערכים לדוגמה (S-0…, ‏P-1…, ‏PO-…, ‏C-1…) ומזהי משתמשים (PL-, ‏AP-).

**השורה התחתונה.** הלוגיקה כללית: המהדר, החוזה, השער והסכמות לא מכירים אף טבלה, עמודה או מדד של הרכש. כל מה שתלוי בתחום מגיע מהקטלוג (`schema.json`, ‏`relations.json`). **התוכן התחומי יושב בשלושה מקומות:** הפרומפטים (משפט פתיחה ודוגמאות), כמה מחרוזות קבועות במסך ובחוזה, ומשתמשי הדמו. אף אחד מהם לא שובר הרצה על סכמה אחרת, אבל הפרומפטים ידחפו את המודל לכיוון רכש.

## סיווג

- **לעבור לקטלוג:** תוכן שבסכמה אחרת יהיה שגוי או מטעה. צריך לבוא מהקטלוג (או מקובץ תחום ליד הקטלוג).
- **מקובל:** תוכן שיישאר נכון בכל תחום, או הערה בקוד שמסבירה בעזרת דוגמה.
- **רק לבדיקה או לדמו:** קוד שרץ רק בכלים, בבדיקות או בדמו המתוסרט.

## הממצאים

### הפרומפטים (`query_assistance/templates/`)

| קובץ | מה יש בו | סיווג |
|---|---|---|
| `intent_analysis.md` (v6) | משפט הפתיחה "a question about supply-chain data". ארבע דוגמאות שלמות על `commitments`, ‏`parts.part_id`, ‏`on_hand`, ‏`late_by_delivery`, ‏S-031, ‏P-11862, ‏PO-87954. | **לעבור לקטלוג.** הדוגמאות מלמדות את המודל שמות עמודות שלא קיימים בסכמה אחרת. |
| `rewrite.md` (v3) | "a conversation about supply-chain records". שלוש דוגמאות עם S-031, ‏PO-87710, ‏P-11862, ‏commitments, ‏late. | **לעבור לקטלוג** (משפט הפתיחה והדוגמאות). הכללים עצמם (3a: "them, their, those") כלליים. |
| `synthesis.md` (v11) | "a question about supply-chain records". דוגמאות עם `late_by_delivery`, ‏`late_by_promise`, ‏supplier_id, ‏purchase order lines, ‏S-038, ‏C-1…; כ-74 מופעים של "late". | **לעבור לקטלוג.** הכללים (הפניות, `{label.total}`, יחידות) כלליים. |
| `synthesis_redraft.md` (v8) | "supply-chain records", ודוגמה אחת על ספקים מאחרים. | **לעבור לקטלוג.** |
| `gate.md` (v6) | כלל 5: "What you know about suppliers, parts or the world is not support". שלוש דוגמאות על S-031, ‏S-021, ‏C-100512 ו-purchase order lines. | **לעבור לקטלוג** (הדוגמאות). בכלל 5 אפשר להשמיט את "suppliers, parts" ולהשאיר "the world": שינוי נוסח, לא קטלוג. |

- **הפרומפטים מוקפאים לדמו** (19.10). כל אחד מהשינויים כאן הוא גרסת פרומפט חדשה, ודורש סבב בדיקה (Haiku) לפני מיזוג.
- **הצעה של Code, לא מהספר:** משפט התחום ("supply-chain records") כ-placeholder שמתמלא מהקטלוג (למשל `domain` ב-`schema.json`), והדוגמאות כקובץ דוגמאות לכל קטלוג, כמו `demo_data/intent_analysis.md` היום. מאגר השאלות המאומתות (משימה 12) הוא המקום הטבעי שלהן.
- **שני קבצי intent:** השרת משתמש ב-`demo_data/intent_analysis.md` (v9), ו-`query_assistance/templates/intent_analysis.md` (v6) הוא ברירת המחדל של `pipeline.run` כשאין `intent_prompt`. הישן כבר לא משקף את הכללים של סבב התיקונים. כדאי שתהיה תבנית כללית אחת ודוגמאות לכל קטלוג.

### הקוד (`query_assistance/*.py`, ‏`api/*.py`)

| מקום | מה | סיווג |
|---|---|---|
| `contract.py:388` (`NEEDS_CLARIFICATION`) | "…naming what it concerns, such as the period, supplier or part." | **לעבור לקטלוג.** בסכמה אחרת "supplier or part" שגוי. אפשר לבנות את הדוגמה משמות הישויות בקטלוג, או להשמיט אותה. |
| `api/state.py:31` (`DEMO_USERS`) | PL-01 עד PL-05 ו-AP-01, עם התפקידים PLANNER ו-APPROVER. | **רק לדמו.** בשלב ב׳ יוחלף בהזדהות אמיתית (משימה 9). שמות התפקידים עצמם מגיעים מ-`entitlement` בקטלוג, והקוד לא מכיר אותם. |
| `api/routes.py:168` | ברירת המחדל `"AP-01"` למשתמש של דוגמה. | **רק לדמו** (דוגמאות מתוסרטות). |
| `query_assistance/dry_run.py:43–64, 106, 160` | שאלה, תוכנית ותשובה על ספקים, ‏`{"role": "APPROVER", "user_id": "AP-01"}`. | **רק לבדיקה** (כלי עלות). יושב ב-`query_assistance/`, אז כדאי להעביר ל-`tools/`. |
| `query_assistance/schema.json`, ‏`relations.json` | הקטלוג הזמני של בדיקות המהדר (commitments, ‏PLANNER, ‏APPROVER). | **רק לבדיקה.** מסומן PLACEHOLDER. מיקום מטעה: `Catalog.load()` טוען אותו כברירת מחדל (`compiler.py:148`), אז קריאה בלי נתיב נותנת קטלוג רכש. כדאי לחייב נתיב, או להעביר ל-`tests/fixtures/`. |
| `compiler.py:539–540`, ‏`compiler.py:581`, ‏`contract.py:107`, ‏`contract.py:250`, ‏`synthesis.py:16–19` | הערות ו-docstrings עם supplier_id, ‏S-03, ‏part_forecast. | **מקובל.** הסבר בעזרת דוגמה, בלי השפעה על ההרצה. |
| `contract.py:70`, ‏`prompts.py:113` (D7, "me") | "planner = you" נבנה משם העמודה בקטלוג ומ-`user_column`. | **מקובל.** כללי. |
| `medagentpro_schemas.json` | אין ערך תחומי באף `enum`. הערה אחת מזכירה את `commitments.delay_reason` ‏(שורה 820). | **מקובל** (ההערה). |

### המסך (`frontend/`)

| מקום | מה | סיווג |
|---|---|---|
| `app/layout.tsx:21` | "Query assistance over supply-chain records (demo)". | **לעבור לקטלוג** (או ל-`/api/health`). |
| `app/components/Chrome.tsx:48` | התגית "Supply chain" בכותרת. | **לעבור לקטלוג.** |
| `app/components/Composer.tsx:35` | "Ask about suppliers, orders, parts or deliveries". | **לעבור לקטלוג.** |
| `app/components/Chat.tsx:280` | טקסט המסך הריק במודל האמיתי: "Ask about suppliers, purchase orders and their lines, goods receipts, parts and their stock…". | **לעבור לקטלוג.** רשימה קבועה של הטבלאות, שתהיה שגויה ברגע שהקטלוג משתנה. השרת יכול להחזיר את שמות הישויות שהמשתמש רואה (`prompts.visible_tables`). |
| `app/components/UserPicker.tsx:11–12, 162–170`, ‏`lib/types.ts:145–150`, ‏`Chat.tsx:39` | המשתמשים והתפקידים, ו-"sees all orders" / "own orders only". | **רק לדמו**, ⚠️ **ויש כאן אי-התאמה:** ‏D6 (18.10) קבע שהמתכנן בדמו רואה את כל הנתונים, אבל הבורר עדיין אומר "Sees only their own orders" ו-"PLANNERS · OWN ORDERS ONLY". זה מטעה מציג וקהל. תיקון טקסט קטן. |
| `app/components/Examples.tsx:11` | הערה על "as PL-02". | **רק לדמו.** |

## מה הסריקה לא מצאה, וגבול אחד שאינו תחומי

- **לא נמצא:** שם טבלה, עמודה או מדד של הרכש בתנאי, בבחירת ענף או בבניית SQL. אין ערך תחומי ב-`enum` של הסכמות.
- ⚠️ **גבול כלליות שאינו תחום:** הסכמות דורשות שם טבלה, עמודה ותווית `^[a-z][a-z0-9_]{0,39}$` (‏`$defs/name`, ‏`$defs/qualified`, ‏`reference`). מסדי BIRD רבים משתמשים באותיות גדולות, ברווחים ובשמות ארוכים יותר. כדי להריץ על סכמה כזו (משימה 11, החלק השני) צריך כינוי בטוח לכל שם בקטלוג, שהמהדר ממפה לשם האמיתי. **זו שאלה לצ׳אט:** זה שינוי בסכמה ובמה שנשלח לספק.

---

# מיפוי ערכים: איך מונח של המשתמש מגיע לערך במסד (משימה 13a, ‏19.10)

**דוח בלבד. שום קוד לא שונה.** הבדיקה: קריאת `compiler.py`, ‏`prompts.py`, ‏`demo_data/schema.json` ו-`demo_data/intent_analysis.md`, והרצת המהדר על `demo.db` עם תוכניות שכתבתי ביד. בלי מודל.

**השורה התחתונה.** היום אין שום מיפוי בקוד. **המודל הוא שממפה,** ממה שהקטלוג מראה לו. הקוד לא בודק את הערך שהמודל בחר מול הסט הסגור, לא מתעלם מאותיות גדולות, ולא קורא ערכים אמיתיים מהמסד. ערך שלא תואם בדיוק נגמר בתוצאה ריקה. ובמזהה הוא נגמר ב"לא קיים", וזה לא נכון.

## שלושה מסלולים, לפי סוג המונח

| המונח | איך הוא מגיע למסד היום | איפה זה מוגדר |
|---|---|---|
| **מושג עם הגדרה** ("late", ‏"below reorder point") | המודל בוחר **מדד** מהקטלוג (`metrics`: ‏`late_by_delivery`, ‏`late_by_promise`, ‏`missed_promise` וכו׳), והמהדר מרכיב את הביטוי. מונח עם כמה מדדים מתאימים נכנס ל-`ambiguous`, וכל קריאה מוצגת. | `schema.json` → `metrics`, עם `label`, ‏`description` ו-`description_negated`. **זה המיפוי היחיד שהוא באמת בקטלוג.** |
| **ערך מסט סגור** ("open", ‏"EMEA", ‏"Tubing") | הקטלוג מצהיר `values` על עמודה (`purchase_orders.status`: ‏open, closed, cancelled; ‏`suppliers.region`; ‏`parts.category`). ‏`prompts._column_note` מדפיס אותם לפרומפט ("one of: open, closed, cancelled"), וכלל 10 בפרומפט אומר "use one of them exactly". | `schema.json` → `values`. **מוצג למודל, לא נאכף בקוד.** |
| **שם חופשי או מזהה** ("Acme", ‏"S-001") | המודל מעתיק את הערך לתוכנית ("Use identifiers exactly as they appear in the question", כלל 10). המהדר בודק שהמזהה הופיע בשיחה (NFR 27), ומריץ בדיקת קיום (`_probes`) במקום שבו המזהה מוחזק. | אין. אין `values` על `suppliers.name`, ואין חיפוש. |

## מה ההרצה הראתה (`demo.db`, ‏AP-01)

| תוכנית | תוצאה | מה המשתמש רואה |
|---|---|---|
| `purchase_orders.status = 'open'` | ‏236 | תשובה. |
| `purchase_orders.status = 'Open'` | ‏0. **המהדר מקבל אותו** | ‏"No records matched", ו-Examined אומר 'Open'. אין שגיאה, ואין רמז שהערך לא בסט. |
| `purchase_orders.status = 'opened'` | ‏0, אותו דבר | אותו דבר. |
| `suppliers.name = 'Apex Polymers'` | ‏1 | תשובה. |
| `suppliers.name = 'Apex'` או `'apex polymers'` | ‏0 | ‏"No records matched". אין התאמה חלקית (אין `LIKE` בשפת התוכנית, ‏`CMP_SQL` רק EQ עד LTE), ו-`=` ב-SQLite רגיש לאותיות. |
| `suppliers.supplier_id = 's-001'` | ‏0, **ובדיקת הקיום נכשלת** | ⚠️ **"s-001 does not appear in suppliers".** המזהה קיים (S-001). בדיקת המקור (NFR 27) לא רגישה לאותיות (`casefold`), אבל בדיקת הקיום והשליפה רגישות. **זו טענה שגויה שהמערכת מציגה כעובדה** (מבחן 35). |

## מה זה אומר

- **"open":** הסט הסגור מגן רק כל עוד המודל מציית. מודל קטן יותר (Haiku, משימה 1) יציית פחות. **הגנה זולה בקוד, בלי מודל:** המהדר דוחה ערך שלא ב-`values` (‏`CompileError`, ומשם ניסיון משוב עם הנתיב בלבד), או מיישר אותיות לערך מהסט. **זו הצעה של Code, לא מהספר.** הספר (NFR 6) דורש לבדוק את התוכנית מול הסכמה. אם `values` הוא חלק מהסכמה, זו השלמה של NFR 6 ולא דרישה חדשה.
- **"s-001":** באג, לא חוסר. תשובה "לא קיים" על מזהה קיים סותרת את מבחן 35. **התיקון הקטן:** השוואת מזהה בלי תלות באותיות (`COLLATE NOCASE` בשליפה ובבדיקת הקיום), או יישור המזהה לצורה שבמסד לפני השליפה. **השאלה לצ׳אט:** האם זה מותר בלי שינוי בספר. זה נוגע בבדיקת הקיום (מבחן 35) ובבדיקת המקור (NFR 27).
- **"Acme":** אין היום שום דרך לענות על שם חלקי. מסמך המחקר (סעיף 8, שורת "מיפוי ערכים"): ‏Snowflake מחפש ערכים אמיתיים במסד ("USA" לערך ששמור בטבלה). המסמך מונה את מיפוי הערכים כאחד משלושת הפערים שלנו מול השוק. **שתי השאלות לצ׳אט:**
  1. האם ערכים מהמסד מותר לשלוח למודל. היום רק הקטלוג מגיע לתכנון, וטקסט מרשומה לא מגיע לשלבים שמחליטים מה לשלוף (NFR 21).
  2. האם `LIKE` או התאמה חלקית נכנסים לשפת התוכנית. זה שינוי בסכמה 1 ובמה שנשלח לספק.
- **"late":** עובד כמתוכנן, דרך מדדים. ‏"open" כמושג (H3, ‏B1: נקרא כ"לא סופק") הוא בדיוק משימה 6: להצהיר מדד `open` בקטלוג, כך ש-"open" ייבחר כמושג מוגדר ולא כפירוש של המודל.
