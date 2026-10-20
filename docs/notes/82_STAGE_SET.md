# MedAgentPro — סט בדיקות לפי שלבי הצינור (משימה 14, צ׳אט 20.10)

**המקור:** סעיף 8 במסמך המחקר, "השוואה למוצרים המובילים" (Claude Docs, "MedAgentPro — מחקר לשלב ב׳", 19.10). השורות הועתקו מהמסמך כמו שהן, בלי לשנות את הנוסח.

**הסט:** ‏`backend/demo_data/stage_set.json`. הרצה: `python -m tools.real_eval --plans --set stage`, בלי מודל. **הסט מודד איפה אנחנו עומדים, ולא מוכיח הצלחה:** שאלה שידוע שהיא נכשלת היום מסומנת `expected_gap`.
- ‏`where: plan`: אין היום תוכנית שמבטאת את השאלה נכון.
- ‏`where: model`: התוכנית עוברת, אבל פרומפט הדמו (@9) לא מוביל את המודל אליה.

שאלה שמופיעה בסט הדמו או ב-offscript מופיעה כאן כ-`ref`, ולא מועתקת. הטוען מושך אותה, את התוכנית שלה ואת הציפיות שלה מהסט המקורי.

**הציפיות לכל שלב:** ‏`disposition`, מספר הקריאות (`readings`), ‏`compile` (‏`ok`, או קוד של `CompileError` שהתוכנית נכתבה לפגוש), ו-`result` לכל קריאה. ‏`gate` הוא תיעוד בלבד: ב-`--plans` אין מודל ואין שער, והוא נבדק רק בריצה אמיתית.

## השורות שבסט

| שלב | מה עושים בשוק | אצלנו | מצב | category | שאלות |
|---|---|---|---|---|---|
| שכבה סמנטית | כולם דורשים מודל שמנהל נתונים מגדיר: שמות, נרדפות, מדדים והוראות (מודל סמנטי ב-Snowflake, ‏LookML, הוראות סוכן ב-Genie וב-Fabric) | קטלוג schema.json עם מדדים ותיאורים | שווה | `semantic_layer` | `SL1` Which lines were late by promise in week 5?<br>`SL2` Which lines in week 5 were not delivered late?<br>`SL3` → `offscript:C4` |
| מיפוי ערכים | Snowflake מחפש ערכים אמיתיים במסד ("USA" לערך ששמור בטבלה) | לא נבדק. לוודא בקוד | כנראה פער | `value_mapping` | `VM1` What did apex deliver in September? ‏(פער ידוע, model: VM-intent@9)<br>`VM2` What is on hand for Strain relief? ‏(פער ידוע, model: VM-intent@9)<br>`VM3` Which purchase orders with S-014 are Open?<br>`VM4` What did s-001 supply in week 6? |
| סיווג ועמימות | Snowflake מסווג שאלות, דוחה עמומה ומציע שאלות דומות. Genie ו-Looker שואלים שאלת בירור | קריאות מרובות, בקשת בירור, פער מנוסח | שווה ומעל: עונים על כל קריאה במקום לדחות | `classification_ambiguity` | `CA1` → `demo:Q01`<br>`CA2` → `offscript:F3`<br>`CA3` → `offscript:E2` |
| בניית השאילתה | Snowflake ו-Fabric: המודל כותב SQL (Snowflake על סכמה לוגית מפושטת שמתורגמת לפיזית). Looker: המודל ממפה לשדות LookML. Spotter עובד על מטא-דאטה בלבד | המודל ממלא טופס, הקוד בונה SQL | אותה גישה כמו Looker ו-Spotter | `query_building` | `QB1` → `offscript:C2`<br>`QB2` Which suppliers had more than five late lines in weeks 1 to 13?<br>`QB3` Which suppliers had no late lines in weeks 1 to 13? ‏(פער ידוע, plan: task3-EXISTS) |
| אימות ותיקון | Snowflake: לולאת תיקון מול הקומפיילר. Looker: עד 3 ניסיונות | אימות בקומפיילר וניסיון חוזר | שווה | `validation_repair` | `VR1` Which lines from S-001 came with a carrier?<br>`VR2` What did Apex or S-002 supply?<br>`VR3` Which suppliers were late in week 2? |
| הרשאות | Fabric ו-Looker מריצים בהרשאות המשתמש, עם אבטחה ברמת שורה. Looker מזהיר שבחירת טבלה אינה בקרת אבטחה | הקומפיילר מזריק הרשאה, המודל לא נוגע בה | שווה | `entitlement` | `EN1` → `demo:Q14`<br>`EN2` → `offscript:G1`<br>`EN3` Which lines does my supplier S-001 have? |
| שאלות המשך | כולם שומרים הקשר רק בתוך שיחה. Fabric מתעד שההיסטוריה עלולה להתאפס ושתשובות קודמות מגבילות את ההמשך, וממליץ לפתוח שיחה חדשה | שכתוב לשאלה עצמאית | שווה. תשובות עם שם (משימה 3) יעבירו אותנו אותם | `follow_ups` | `FU1` → `offscript:F1a`<br>`FU2` → `offscript:F1b`<br>`FU3` → `offscript:F1c`<br>`FU4` → `offscript:F2` |
| שאלה מרובת חלקים | Looker מריץ כמה שאילתות ומשלב את התוצאות | אין פירוק (H4) | פער (משימה 4) | `multi_part` | `MP1` → `offscript:D2`<br>`MP2` → `demo:Q05`<br>`MP3` Which suppliers delivered late in week 7, and which parts are below their reorder point? ‏(פער ידוע, model: H4)<br>`MP4` → `probe:H4` (משימה 4, ‏20.10, ‏22:30) |
| שקיפות | Genie: פרשנות השאלה ו-"Show code". Looker: "How was this calculated?" עם שדות ומסננים | Examined, הפאנל, מקור לכל משפט | מעל השוק | `transparency` | `TR1` → `offscript:A1`<br>`TR2` → `offscript:A2`<br>`TR3` → `offscript:B2` |
| אימות התשובה | אף אחד לא מתעד בדיקה שהתשובה נתמכת בנתונים. כולם מזהירים שתשובה עלולה להיות שגויה | שער תמיכה וניסוח מחדש | מעל השוק | `answer_verification` | `AV1` → `demo:Q10`<br>`AV2` → `demo:Q11`<br>`AV3` → `offscript:C1` |
| הגבלת שורות | Fabric: ‏25 שורות ו-25 עמודות. Looker: ‏5,000 | 30 | שווה | `row_limit` | `RL1` → `demo:Q03`<br>`RL2` → `offscript:C3`<br>`RL3` Which lines were delivered in September? |
| שאלות "למה" | Genie ו-Spotter Classic לא תומכים | לא בשלב א׳ | שווה. זה צינור החקירה | `why_questions` | `WH1` → `demo:Q07`<br>`WH2` → `offscript:A4`<br>`WH3` → `offscript:E4` |

## השורות שמחוץ לסט

- **דוגמאות מאומתות** (מצב במסמך: פער). זו יכולת שאין לנו (משימה 12), ולא שלב בצינור שאפשר לבדוק בתוכנית. כשמשימה 12 תיבנה, השורה תיכנס לסט.
- **כמה מועמדים** (מצב במסמך: פער אופציונלי (סעיף 2)). אנחנו מריצים מועמד אחד. השוואה בין מועמדים היא ניסוי (משימות 2 ו-8), ולא שאלה עם תוכנית אחת.
- **משוב ושיפור** (מצב במסמך: פער קטן). הדירוג (FR 12) נרשם, ואין לולאת שיפור. אין לזה שאלה ותוכנית.

## המצב, `--plans` (20.10)

‏38 שאלות, 24 מהן הפניות. ל-37 יש תוכנית שעוברת. הפער היחיד בתוכנית הוא `QB3`: תנאי על רשומות אחרות (NOT EXISTS). קריאה לא יכולה לבטא אותו, ותוכנית חייבת להחזיק לפחות קריאה אחת, ולכן אפילו פער כן לבדו הוא לא תוכנית תקינה (משימה 3).

שלושה פערים בצד המודל: `VM1` ו-`VM2`, כי @9 לא מלמד `MATCHES` (‏@10 מלמד, לבדיקות בלבד), ו-`MP3` (‏H4), כי שום דבר בקוד לא בודק שכל חלק בשאלה כוסה (משימה 4).
