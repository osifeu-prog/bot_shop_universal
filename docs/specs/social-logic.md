# BizNet / Buy_My_Shop  לוגיקת אקוסיסטם

## שכבות מערכת

1. Telegram Bot (main.py)
   - רישום משתמשים (`store_user`)
   - תשלומים (`log_payment`, `update_payment_status`)
   - רפרלים (`add_referral`, `get_top_referrers`)
   - Rewards (`create_reward`)
   - סטטיסטיקות (`get_approval_stats`, `get_monthly_payments`)
   - Webhook: /webhook (FastAPI)

2. FastAPI (main.py)
   - /webhook  נקודת כניסה לעדכוני טלגרם
   - /health  ניטור ל-Railway
   - /admin/stats  API JSON לסטטיסטיקות (עם ADMIN_DASH_TOKEN)
   - /site  מגיש את docs/index.html (BizNet)
   - /  שורש האתר (מגיש גם את docs/index.html)
   - /admin/dashboard  HTML שמציג את /admin/stats

3. Postgres
   - טבלאות קיימות (ב-db.py):
     - payments
     - users
     - referrals
     - rewards
     - metrics
   - הרחבה עתידית לרשת חברתית:
     - social_profiles (קישור בין user_id טלגרם לפרופיל באתר)
     - social_wallets (ארנקי BNB / TON)
     - social_activity (פוסטים / לייקים / שיתופים)
     - social_access (מי קנה גישה ממי ובאיזה מחיר)

## זרימת משתמש

1. משתמש נכנס לבוט בטלגרם  /start
2. משלם (בנק / ביט / PayBox / TON) ושולח צילום  `handle_payment_photo`
3. התשלום נרשם כ-pending ב-DB + נשלח לקבוצת הלוגים
4. אדמין מאשר / דוחה (`/approve`, `/reject` או כפתורים)
5. בעת אישור:
   - `update_payment_status(..., "approved")`
   - נשלח לינק לקבוצת הקהילה
   - נשלחת תמונה ממוספרת (NFT-style)  `send_start_image(..., mode="download")`
6. בעתיד:
   - לאחר X שיתופים / מכירות, המשתמש יקבל גישה ל"אזור אישי" באתר (BizNet)
   - שם יוכל למכור גישה משלו, לקשר ארנקים, לראות סטטיסטיקות, וכו'.

## חיבור אתר  טלגרם

- האתר (`/site`) עובד כרשת חברתית חינמית:
  - משתמש יכול לדפדף, ליצור פוסטים (בשלב ראשון רק localStorage).
- לאחר תשלום ואישור בוט:
  - נוצרת רשומה ב-DB שמשייכת את user_id של טלגרם לפרופיל באתר.
  - בעתיד: Login דרך Telegram (ב-frontend)  קריאה ל-API שמזהה את המשתמש האמיתי.
- כל הנתונים החזקים (תשלומים, רפרלים, Rewards) נשמרים ב-Postgres.
- האתר מציג נתוני high-level (מיהו, כמה שיתף, כמה מכר, מאיזה מחיר).

## המשך פיתוח

- יצירת מודול social_api.py עם FastAPI Router:
  - /api/social/profile
  - /api/social/activity
  - /api/social/referrals
- יצירת טבלאות social_* ב-Postgres דרך db.py.
- הרחבת ה-JS ב-docs/index.html לקרוא ל-API במקום רק localStorage.
