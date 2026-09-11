"""کارمزد و مالیات معاملات آپشن — یک محاسبه، چند مصرف‌کننده.

**چرا مهم است**

حد سود پیشنهادی بدون کارمزد، خوش‌بینانه است. روی معامله‌ی کوچک اختلافش
ناچیز است، ولی روی اسپردی که سودش ۲٪ پرمیوم است، کارمزدِ رفت و برگشت
می‌تواند کلِ سود را ببرد. سیگنالی که «سودده» نشان داده شود و در عمل سر
به سر باشد، از سیگنال نداشتن بدتر است.

**قرارداد کلیدی: پیش‌فرض صفر است.**

نرخ واقعی کارمزد آپشن بورس تهران در این پروژه نیست و **حدس نمی‌زنیم** —
همان قاعده‌ای که برای داده‌ی بازار رعایت می‌شود. تا کاربر نرخ را ندهد،
کارمزد صفر است و همه‌ی اعداد دقیقاً مثل قبل می‌مانند. یک نرخِ حدسی،
دقتِ کاذب می‌سازد که از نبودش بدتر است.

اگر نرخ داده شود، در متن سیگنال هم دیده می‌شود تا معلوم باشد اعداد
شاملش هستند.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeSchedule:
    """نرخ کارمزد و مالیات، به‌صورت **کسر** (نه درصد).

    مثال: `0.001` یعنی ۰٫۱٪.

    تفکیک خرید و فروش عمدی است: در بورس تهران این دو معمولاً برابر
    نیستند، و مالیات فقط سمت فروش است.
    """

    #: کارمزد سمت خرید (کارگزاری + بورس + سپرده‌گذاری)
    buy_rate: float = 0.0
    #: کارمزد سمت فروش
    sell_rate: float = 0.0
    #: مالیات، فقط روی فروش
    sell_tax_rate: float = 0.0
    #: کارمزد ثابت هر سفارش (اگر کارگزاری داشته باشد)
    per_order: float = 0.0
    #: کاربر **صریحاً** گفته که نرخ‌ها همین‌هاست — حتی اگر همه صفر باشند.
    #:
    #: بدون این، «صفرِ پیش‌فرضِ پروژه» از «صفرِ واقعیِ اعلام‌شده» قابل
    #: تفکیک نبود و هر دو یک‌جور خوانده می‌شدند. معامله‌ای که زیر صفرِ
    #: پیش‌فرض انجام شود هزینه‌اش **دانسته نیست**؛ زیر صفرِ اعلام‌شده،
    #: هست و صفر است.
    declared: bool = False

    @property
    def is_zero(self) -> bool:
        """آیا هیچ نرخی تنظیم نشده؟ (پیش‌فرض پروژه)"""
        return not any(
            (self.buy_rate, self.sell_rate, self.sell_tax_rate, self.per_order)
        )

    @property
    def rates_known(self) -> bool:
        """آیا هزینه‌ی یک عملیات با این نرخ‌ها **دانسته** است؟

        نرخ ناصفر خودش اعلام است. صفر فقط وقتی اعلام حساب می‌شود که
        کاربر صریحاً `declared` را داده باشد؛ وگرنه همان پیش‌فرضِ پروژه
        است و یعنی «نمی‌دانیم»، نه «صفر است».
        """
        return self.declared or not self.is_zero

    @property
    def round_trip_rate(self) -> float:
        """مجموع نرخ رفت و برگشت — عددی که واقعاً از سود کم می‌شود."""
        return self.buy_rate + self.sell_rate + self.sell_tax_rate

    # ------------------------------------------------------------------
    def entry_cost(self, notional: float, is_buy: bool) -> float:
        """کارمزد ورود به موقعیت.

        `notional` ارزش کل است (پرمیوم × تعداد × اندازه‌ی قرارداد).
        """
        notional = abs(notional)
        rate = self.buy_rate if is_buy else (self.sell_rate + self.sell_tax_rate)
        return notional * rate + self.per_order

    def exit_cost(self, notional: float, was_buy: bool) -> float:
        """کارمزد خروج — سمتش **برعکس** ورود است.

        خریدار برای خروج می‌فروشد (پس مالیات فروش می‌دهد) و فروشنده برای
        خروج می‌خرد. وارونگی سمت، اشتباه رایجی است که کارمزد را کم‌برآورد
        می‌کند.
        """
        notional = abs(notional)
        rate = (self.sell_rate + self.sell_tax_rate) if was_buy else self.buy_rate
        return notional * rate + self.per_order

    def round_trip_cost(self, notional: float, is_buy: bool = True) -> float:
        """کارمزد کل یک معامله‌ی کامل (ورود + خروج)."""
        return self.entry_cost(notional, is_buy) + self.exit_cost(notional, is_buy)

    # ------------------------------------------------------------------
    def breakeven_premium(self, premium: float, is_buy: bool = True) -> float:
        """پرمیومی که در آن معامله واقعاً سر به سر است.

        خریدار باید بالاتر از پرمیوم ورودی بفروشد تا کارمزد را هم پوشش
        بدهد؛ فروشنده باید پایین‌تر بخرد.
        """
        if premium <= 0:
            return premium
        shift = premium * self.round_trip_rate
        return premium + shift if is_buy else premium - shift

    def net_take_profit(self, premium: float, target_pct: float, is_buy: bool) -> float:
        """حد سودی که **پس از کارمزد** به درصد هدف برسد.

        بدون این، حد سودِ «۷۰٪» در عمل کمتر می‌شد و کاربر نمی‌فهمید چرا.
        """
        if premium <= 0:
            return premium
        gross = premium * (1 + target_pct / 100.0) if is_buy else premium * (
            1 - target_pct / 100.0
        )
        shift = premium * self.round_trip_rate
        return gross + shift if is_buy else gross - shift

    def describe(self) -> str:
        """توضیح انسان‌خوان برای متن سیگنال. خالی اگر نرخی تنظیم نشده باشد."""
        if self.is_zero:
            return ""
        parts = []
        if self.buy_rate:
            parts.append(f"خرید {self.buy_rate * 100:.3f}٪")
        if self.sell_rate:
            parts.append(f"فروش {self.sell_rate * 100:.3f}٪")
        if self.sell_tax_rate:
            parts.append(f"مالیات {self.sell_tax_rate * 100:.3f}٪")
        if self.per_order:
            parts.append(f"ثابت {self.per_order:,.0f}")
        return "کارمزد: " + "، ".join(parts)


#: پیش‌فرض پروژه: هیچ نرخی حدس زده نمی‌شود.
NO_FEES = FeeSchedule()
