# Kh-Optionbaz

میزکار **شخصی و محلی** تحلیل و ارزیابی اختیار معامله‌ی بازار ایران.

> ⚠️ این ابزار **سفارش ثبت نمی‌کند.** خروجی آن تحلیل و پیشنهاد است؛ اجرای
> معامله دستی و بر عهده‌ی کاربر است. اجرای واقعی سفارش در محدوده‌ی فعلی
> محصول نیست.

منشأ کد، کامیت مبنا و وضعیت اعلان مجوز: [PROVENANCE.md](PROVENANCE.md)
پروژه‌ی مبنا: <https://github.com/OmidRafiee/GarnetTrader>

---

## هدف محصول

یک میزکار تصمیم‌گیری که به این پرسش‌ها جواب بدهد:

- زنجیره‌ی اختیار و داده‌ی بازار الان چه می‌گوید؟
- کدام فرصت‌ها با توجه به **نقدشوندگی، هزینه‌ی اجرا، سرمایه‌ی لازم و
  ریسک** ارزش نگاه‌کردن دارند؟
- وضعیت بازار چیست (روند صعودی، نزولی، رنج، پرنوسان)؟
- توزیع احتمالی نماد پایه تا سررسید هر قرارداد چگونه است؟
- برای **این معامله‌ی مشخص**: احتمال سود، بازده خالص مورد انتظار، و زیان
  سناریوهای بد چقدر است؟
- اخبار مرتبط (از یک سرویس مستقل و لوکال) چه می‌گویند؟

با شخصی‌سازی بر پایه‌ی سرمایه، دارایی پایه، تحمل زیان و محدودیت‌های کاربر.

سه اصل که کل مسیر توسعه را مقید می‌کنند:

1. **پیش‌بینی جهت**، **احتمال سود معامله** و **امتیاز قدرت سیگنال** سه
   چیز جدا هستند و نباید در یک عدد قاطی شوند.
2. هیچ نرخ برد یا مزیت معاملاتی بدون داده و اعتبارسنجی وعده داده نمی‌شود.
3. هر عددی که نمایش داده می‌شود یا قابل بازسازی است، یا صریحاً
   «نامعلوم» است.

وضعیت فعلی محصول و آنچه هنوز قابل استناد نیست:
[docs/known-limitations.md](docs/known-limitations.md)
مسیر پیشِ رو: [docs/roadmap.md](docs/roadmap.md)

---

## اجرای محلی

کد پروژه در پوشه‌ی [`option_signal_bot/`](option_signal_bot/) است و
مستندات تفصیلی خودِ ربات در
[`option_signal_bot/README.md`](option_signal_bot/README.md) قرار دارد.

> ⚠️ بخش‌هایی از README پروژه‌ی مبنا اعدادی از بک‌تست و گزارش عملکرد نقل
> می‌کنند که **فعلاً قابل استناد نیستند**. پیش از اتکا به هر عددی،
> [docs/known-limitations.md](docs/known-limitations.md) را بخوانید.

### پیش‌نیاز

- Python **3.11** یا بالاتر
- برای هسته‌ی ربات: فقط `PyYAML`
- برای داشبورد وب: `fastapi` و `uvicorn`
- برای تست: `pytest` و `httpx`

`pandas`، `numpy` و `scipy` در `requirements.txt` هستند ولی مسیرهای
اصلی به آن‌ها نیاز ندارند (قیمت‌گذاری با کتابخانه‌ی استاندارد کار می‌کند).
`pytse-client` هم فقط برای provider جایگزین `pytse` لازم است که مسیر
اصلی نیست.

```powershell
cd option_signal_bot
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install PyYAML fastapi uvicorn pytest httpx
```

### تنظیمات

```powershell
copy config\settings.example.yaml config\settings.yaml
```

`config/settings.yaml` در `.gitignore` است و **هرگز نباید کامیت شود**.

### اجرا با داده‌ی ضبط‌شده (بدون شبکه، بدون حساب)

سریع‌ترین راه برای اطمینان از سالم‌بودن نصب. در `settings.yaml`:

```yaml
option_chain:
  provider: fixture
  fixture_path: tests/fixtures/tsetmc_option_market_watch.json
market_data:
  provider: tsetmc
  fixture_path: tests/fixtures/tsetmc_option_market_watch.json
  history_dir: tests/fixtures/history
  symbols: [خودرو, شستا, اهرم]
trading_calendar:
  learn_from_market: false
general:
  run_only_when_market_open: false
```

```powershell
.\.venv\Scripts\python.exe main.py --config config\settings.yaml --once
```

این حالت روی پاسخ‌های واقعیِ **ضبط‌شده‌ی** TSETMC اجرا می‌شود و هیچ
درخواست شبکه‌ای نمی‌فرستد.

### اجرا با داده‌ی زنده

`provider: tsetmc` بدون `fixture_path`. داده از API عمومی TSETMC خوانده
می‌شود — **بدون نیاز به لاگین، توکن یا حساب کارگزاری**.

```powershell
.\.venv\Scripts\python.exe main.py --config config\settings.yaml --once
```

### داشبورد وب

```powershell
.\.venv\Scripts\python.exe -m web
```

روی `http://127.0.0.1:8787` باز می‌شود.

> 🔒 داشبورد **احراز هویت ندارد** و تنظیمات را می‌نویسد. فقط روی
> `127.0.0.1` گوش می‌دهد؛ روی شبکه بازش نکنید.

### تست

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

تست‌ها روی داده‌ی ضبط‌شده اجرا می‌شوند و به شبکه، ساعت بازار یا هیچ
توکنی نیاز ندارند.

---

## مرزهای ایمنی

این قیدها در کد اعمال و با تست تضمین شده‌اند و باید حفظ شوند:

| قید | جای اعمال |
|---|---|
| هیچ ماژولی بیرون از `execution/` آن را import نمی‌کند (جز `web/api.py` برای معاملات کاغذی) | گارد AST در `tests/test_signal_generator.py` |
| هیچ تستی به شبکه نمی‌رود | `tests/test_no_network_guard.py` |
| استراتژی به نوتیفایر، دیتابیس یا لایه‌ی اجرا دسترسی ندارد | `StrategyContext` + گارد AST |
| اتصال کارگزاری فقط‌خواندنی است و پیش‌فرض خاموش | `broker.enabled: false` |
| اجرای واقعی سفارش پیاده‌سازی نشده | `execution.enabled: false` |

**هرگز کامیت نکنید:** `config/settings.yaml`، فایل نشست کارگزاری، توکن
تلگرام، فایل HAR، محتویات `var/` یا هر داده‌ی حساب شخصی.
