# Fast Scripts — Test Checklist

| # | סקריפט | תיאור | סטטוס | הערות |
|---|--------|--------|-------|-------|
| **📱 WhatsApp** | | | | |
| 1 | `fast_whatsapp.py` | שליחה לאיש קשר בודד | ✅ | תוקן: endpoint /api/send + שדה recipient, JID-based send |
| 2 | `fast_wa_group.py` | שליחה לקבוצה | ✅ | fuzzy match (partial_ratio) — מוצא Café Group מ-"cafe group" |
| 3 | `fast_wa_history.py` | N הודעות אחרונות עם איש קשר | ✅ | LID support + resolve_name fix |
| 4 | `fast_wa_search.py` | חיפוש מילה בשיחת וואטסאפ | ✅ | LID support, name resolve |
| **🔔 תזכורות** | | | | |
| 5 | `fast_reminder_create.py` | יצירת תזכורת חדשה | ✅ | |
| 6 | `fast_reminder_cancel.py` | ביטול תזכורת | ✅ | תוקן: status=dismissed (לא cancelled) |
| 7 | `fast_reminder_list.py` | רשימת תזכורות קרובות | ✅ | |
| 8 | `fast_reminder_update.py` | עדכון תזכורת (זמן / שם / שניהם) | ✅ | כל 3 מצבים נבדקו |
| **✅ משימות** | | | | |
| 9 | `fast_task_create.py` | יצירת משימה חדשה | ✅ | domain חייב להיות מהרשימה הקיימת |
| 10 | `fast_task_done.py` | סימון משימה כהושלמה | ✅ | |
| 11 | `fast_task_update.py` | עדכון עדיפות / תאריך יעד | ✅ | |
| 12 | `fast_tasks_list.py` | רשימת משימות (עם פילטרים) | ✅ | |
| **👤 אנשי קשר** | | | | |
| 13 | `fast_contacts_lookup.py` | חיפוש פרטי איש קשר | ✅ | phone, nicknames, ambiguity |
| 14 | `fast_contacts_create.py` | הוספת איש קשר חדש | ✅ | |
| **📅 יומן** | | | | |
| 15 | `fast_cal_list.py` | אירועי יומן קרובים | ✅ | RSVP + שעת סיום |
| 16 | `fast_cal_create.py` | יצירת אירוע | ✅ | |
| 17 | `fast_cal_update.py` | עדכון אירוע (זמן / מיקום) | ✅ | |
| 18 | `fast_cal_delete.py` | מחיקת אירוע | ✅ | |
| **⚙️ מערכת** | | | | |
| 19 | `fast_action_log.py` | לוג פעולות אחרונות | ✅ | |
| 20 | `fast_memory_save.py` | שמירת עובדה בזיכרון | ✅ | |

## סטטוסים

- 🔲 — לא נבדק
- ✅ — עובד
- ❌ — נכשל (ראה הערות)
- 🔧 — בתיקון

## הוראות בדיקה

לכל סקריפט, שלח הודעה טלגרם שתפעיל אותו דרך הדיספאצ'ר, ובדוק:
1. הודעת ה-ACK נשלחה ✓
2. אינדיקטור הקלדה פועל ✓
3. תוצאה נכונה מתקבלת ✓
4. ACK נמחק ✓
5. אינדיקטור הקלדה נעצר ✓
