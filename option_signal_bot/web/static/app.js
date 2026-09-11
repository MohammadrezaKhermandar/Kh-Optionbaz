/* ---------------------------------------------------------------
   داشبورد GarnetTrader — بدون فریم‌ورک، بدون build
   --------------------------------------------------------------- */
"use strict";

// ------------------------------------------------------------------ utils
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const fmt = (n, digits = 0) =>
  n === null || n === undefined || Number.isNaN(n)
    ? "—"
    : Number(n).toLocaleString("fa-IR", { maximumFractionDigits: digits });

function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show " + kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast " + kind), 3200);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* پاسخ بدون بدنه */
  }
  if (!res.ok) {
    throw new Error((body && body.detail) || `خطای ${res.status}`);
  }
  return body;
}

// ------------------------------------------------------------------ tabs
$("#tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  btn.classList.add("active");
  $("#panel-" + btn.dataset.tab).classList.add("active");
  if (btn.dataset.tab === "strategies") loadStrategies();
  if (btn.dataset.tab === "symbols") { loadDataSource(); loadSymbols(); }
  if (btn.dataset.tab === "risk") loadRisk();
  if (btn.dataset.tab === "account") { loadBrokerSetup(); loadAccount(); }
  if (btn.dataset.tab === "report") loadReport();
  if (btn.dataset.tab === "structures") initStructures();
  if (btn.dataset.tab === "paper") loadPaperTab();
});

// ------------------------------------------------------------------ status
async function loadStatus() {
  const box = $("#status");
  try {
    const s = await api("/api/status");
    box.innerHTML = "";

    // بازار باز است یا نه
    let mk = "نامشخص", mkCls = "chip-muted";
    if (s.market_open === true) { mk = "بازار باز"; mkCls = "chip-ok"; }
    else if (s.market_open === false) { mk = "بازار بسته"; mkCls = "chip-warn"; }
    box.append(el("span", "chip " + mkCls, mk));

    // منبع داده — پروژه داده ساختگی ندارد، پس همیشه واقعی است
    const src = `${s.market_data_provider}+${s.option_chain_provider}`;
    box.append(el("span", "chip chip-ok", "داده واقعی: " + src));

    box.append(el("span", "chip chip-muted", `${fmt(s.signal_count)} سیگنال ذخیره‌شده`));

    // تقویم معاملاتی: وقتی بازار بسته است، مفیدترین خبر «کِی باز می‌شود» است
    if (s.today_jalali) {
      box.append(el("span", "chip chip-muted", "امروز " + s.today_jalali));
    }
    if (s.next_trading_day) {
      box.append(el("span", "chip chip-muted", "روز معاملاتی بعدی: " + s.next_trading_day));
    }
    if (s.known_holidays) {
      box.append(el("span", "chip chip-muted", `${fmt(s.known_holidays)} تعطیلی شناخته‌شده`));
    }

    // آخرین به‌روزرسانی. دو عدد جداست و تفاوتشان مهم است:
    //   server_time    → این صفحه چقدر تازه است
    //   last_signal_at → آخرین باری که ربات واقعاً چیزی پیدا کرد
    // یکی گرفتنشان یعنی کاربر فکر کند ربات تازه رصد کرده در حالی که
    // فقط صفحه رفرش شده.
    if (s.server_time) {
      const t = new Date(s.server_time).toLocaleTimeString("fa-IR");
      box.append(el("span", "chip chip-muted", "به‌روزرسانی: " + t));
    }
    if (s.last_signal_at) {
      const d = new Date(s.last_signal_at);
      const mins = Math.round((Date.now() - d.getTime()) / 60000);
      const ago =
        mins < 1 ? "همین الان" :
        mins < 60 ? `${fmt(mins)} دقیقه پیش` :
        mins < 1440 ? `${fmt(Math.round(mins / 60))} ساعت پیش` :
        d.toLocaleDateString("fa-IR");
      box.append(el("span", "chip chip-muted", "آخرین سیگنال: " + ago));
    }
  } catch (err) {
    box.innerHTML = "";
    box.append(el("span", "chip chip-bad", "خطا: " + err.message));
  }
}

// ------------------------------------------------------------------ signals
let allSignals = [];
//: کش سطح صفحه؛ دکمه‌ی «اجرا با یک کلیک» فقط وقتی معاملات کاغذی روشن است دیده می‌شود
let paperTradingEnabled = false;

async function refreshPaperTradingFlag() {
  try {
    const d = await api("/api/paper-trading/settings");
    paperTradingEnabled = !!d.enabled;
  } catch {
    paperTradingEnabled = false;
  }
}

function signalCard(s) {
  const isCall = s.option_type === "call";
  const card = el("div", "sig " + (isCall ? "call" : "put"));

  const head = el("div", "sig-head");
  head.append(el("span", "sig-badge " + (isCall ? "badge-call" : "badge-put"),
    (s.side === "buy" ? "خرید " : "فروش ") + (isCall ? "Call" : "Put")));
  head.append(el("span", "sig-sym", s.symbol));
  head.append(el("span", "sig-strategy", s.strategy_name));
  const when = s.created_at ? new Date(s.created_at).toLocaleString("fa-IR") : "";
  head.append(el("span", "sig-time", when));
  card.append(head);

  const grid = el("div", "sig-grid");
  const cell = (k, v, cls) => {
    const d = el("div");
    d.append(el("span", "k", k));
    d.append(el("span", "v " + (cls || ""), v));
    return d;
  };
  grid.append(cell("نماد پایه", `${s.underlying || "—"} @ ${fmt(s.underlying_price)}`));
  grid.append(cell("قیمت اعمال", fmt(s.strike)));
  grid.append(cell("سررسید", `${s.expiry} (${fmt(s.days_to_expiry)} روز)`));
  grid.append(cell("پرمیوم", fmt(s.suggested_price)));
  grid.append(cell("تعداد", fmt(s.suggested_qty) + " قرارداد"));
  grid.append(cell("حد ضرر", fmt(s.stop_loss), "v-loss"));
  grid.append(cell("حد سود", fmt(s.take_profit), "v-gain"));
  if (s.confidence !== null && s.confidence !== undefined) {
    grid.append(cell("اعتماد", fmt(s.confidence * 100) + "٪"));
  }
  card.append(grid);

  if (s.reason) card.append(el("div", "sig-reason", s.reason));

  const src = s.metadata && s.metadata.data_source;
  if (src) card.append(el("div", "sig-src", "منبع داده: " + src));

  if (paperTradingEnabled) {
    const actions = el("div", "card-actions");
    const btn = el("button", "btn btn-ghost", "اجرای این سیگنال (کاغذی)");
    btn.addEventListener("click", () => executeSignalAsPaperOrder(s, btn));
    actions.append(btn);
    card.append(actions);
  }

  return card;
}

function renderSignals() {
  const strat = $("#filter-strategy").value;
  const und = $("#filter-underlying").value;
  const list = allSignals.filter(
    (s) => (!strat || s.strategy_name === strat) && (!und || s.underlying === und)
  );

  const box = $("#signals");
  box.innerHTML = "";
  if (!list.length) {
    box.append(el("p", "empty",
      allSignals.length
        ? "با این فیلتر سیگنالی نیست."
        : "هنوز سیگنالی ثبت نشده. «اجرای پاس رصد بازار» را بزنید."));
    return;
  }
  list.forEach((s) => box.append(signalCard(s)));
}

function fillFilters() {
  const strategies = [...new Set(allSignals.map((s) => s.strategy_name))].sort();
  const unders = [...new Set(allSignals.map((s) => s.underlying).filter(Boolean))].sort();

  const keep = (sel, values, allLabel) => {
    const prev = sel.value;
    sel.innerHTML = "";
    sel.append(el("option", "", allLabel));
    sel.firstChild.value = "";
    values.forEach((v) => {
      const o = el("option", "", v);
      o.value = v;
      sel.append(o);
    });
    if (values.includes(prev)) sel.value = prev;
  };
  keep($("#filter-strategy"), strategies, "همه استراتژی‌ها");
  keep($("#filter-underlying"), unders, "همه نمادها");
}

async function loadSignals() {
  try {
    const data = await api("/api/signals?limit=200");
    allSignals = data.signals || [];
    fillFilters();
    renderSignals();
  } catch (err) {
    $("#signals").innerHTML = "";
    $("#signals").append(el("div", "error", "خطا در خواندن سیگنال‌ها: " + err.message));
  }
}

async function scan(quiet = false) {
  const btn = $("#btn-scan");
  const label = btn.dataset.label || btn.textContent;
  btn.dataset.label = label;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>در حال رصد بازار…';
  if (!quiet) $("#scan-result").innerHTML = "";

  try {
    const r = await api("/api/scan", { method: "POST" });
    const when = new Date().toLocaleTimeString("fa-IR");
    $("#scan-result").innerHTML = "";
    $("#scan-result").append(
      el("div", r.generated ? "ok-box" : "hint",
        (r.generated
          ? `پاس رصد تمام شد: ${fmt(r.generated)} سیگنال تولید شد.`
          : "پاس رصد تمام شد؛ شرایط هیچ استراتژی برقرار نبود.") +
        (quiet ? `  (${when})` : ""))
    );
    await Promise.all([loadSignals(), loadStatus()]);
    return r.generated || 0;
  } catch (err) {
    // ۴۰۹ یعنی پاس قبلی هنوز تمام نشده — در حالت زنده کاملاً عادی است
    // و نباید مثل خطا دیده شود، وگرنه کاربر فکر می‌کند چیزی خراب است.
    const busy = /در حال اجراست/.test(err.message);
    if (!(quiet && busy)) {
      $("#scan-result").innerHTML = "";
      $("#scan-result").append(
        el("div", "error", "پاس رصد ناموفق بود: " + err.message));
    }
    return 0;
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

// ------------------------------------------------------------------ live
// رصد زنده = پولینگ خودکار. بازار تهران فقط ۹:۰۰ تا ۱۲:۳۰ باز است، پس
// وقتی بسته باشد پولینگ **خودش می‌ایستد**: زدنِ پاس روی بازار بسته فقط
// همان قیمت‌های دیروز را دوباره می‌خواند و باتری/شبکه را هدر می‌دهد.
let liveTimer = null;
let liveTick = 0;

function liveOn() {
  return liveTimer !== null;
}

function setLiveStatus(text, cls = "") {
  const node = $("#live-status");
  node.className = "note " + cls;
  node.textContent = text;
}

async function liveRun() {
  liveTick += 1;
  const found = await scan(true);

  // اگر بازار بسته شد، خودمان متوقف می‌شویم
  try {
    const st = await api("/api/status");
    if (st.market_open === false) {
      stopLive("بازار بسته است؛ رصد زنده متوقف شد." +
        (st.next_trading_day ? ` روز معاملاتی بعدی: ${st.next_trading_day}` : ""));
      return;
    }
  } catch {
    /* وضعیت نامعلوم — ادامه می‌دهیم، توقف بی‌دلیل بدتر است */
  }

  if (liveOn()) {
    setLiveStatus(
      `رصد زنده روشن — پاس ${fmt(liveTick)}` +
      (found ? ` · ${fmt(found)} سیگنال تازه` : ""),
      "ok");
  }
}

function startLive() {
  if (liveOn()) return;
  const seconds = Number($("#live-interval").value) || 60;
  liveTick = 0;
  liveTimer = setInterval(liveRun, seconds * 1000);
  $("#btn-live").textContent = "■ توقف رصد";
  $("#btn-live").classList.add("btn-primary");
  $("#live-interval").disabled = true;
  setLiveStatus("رصد زنده روشن شد…", "ok");
  liveRun();   // بلافاصله یک بار، نه بعد از N ثانیه انتظار
}

function stopLive(message = "رصد زنده متوقف شد.") {
  if (liveTimer !== null) clearInterval(liveTimer);
  liveTimer = null;
  $("#btn-live").textContent = "▶ رصد زنده";
  $("#btn-live").classList.remove("btn-primary");
  $("#live-interval").disabled = false;
  setLiveStatus(message);
}

$("#btn-live").addEventListener("click", () => (liveOn() ? stopLive() : startLive()));

// تبِ بسته نباید بی‌صدا پولینگ کند؛ کاربر فکر می‌کند خاموش است.
document.addEventListener("visibilitychange", () => {
  if (document.hidden && liveOn()) {
    stopLive("تب پنهان شد؛ رصد زنده متوقف شد.");
  }
});

$("#btn-scan").addEventListener("click", () => scan());
$("#btn-refresh").addEventListener("click", () => { loadSignals(); loadStatus(); });
$("#filter-strategy").addEventListener("change", renderSignals);
$("#filter-underlying").addEventListener("change", renderSignals);

// ------------------------------------------------------------------ strategies
async function loadStrategies() {
  const box = $("#strategies");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  try {
    const { strategies } = await api("/api/strategies");
    box.innerHTML = "";
    strategies.forEach((st) => box.append(strategyCard(st)));
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

function strategyCard(st) {
  const card = el("div", "card");

  const head = el("div", "strat-head");
  head.append(el("span", "strat-name", st.name));

  const sw = el("label", "switch");
  const cb = el("input");
  cb.type = "checkbox";
  cb.checked = st.enabled;
  const lbl = el("span", "label", st.enabled ? "فعال" : "غیرفعال");
  sw.append(cb, el("span", "track"), lbl);
  head.append(sw);
  card.append(head);

  if (st.doc) card.append(el("div", "strat-doc", st.doc));

  cb.addEventListener("change", async () => {
    cb.disabled = true;
    try {
      await api("/api/strategies/" + encodeURIComponent(st.name), {
        method: "PUT",
        body: JSON.stringify({ enabled: cb.checked }),
      });
      lbl.textContent = cb.checked ? "فعال" : "غیرفعال";
      toast(`«${st.name}» ${cb.checked ? "فعال" : "غیرفعال"} شد.`, "ok");
    } catch (err) {
      cb.checked = !cb.checked;
      toast("خطا: " + err.message, "bad");
    } finally {
      cb.disabled = false;
    }
  });

  // پارامترها
  const params = el("div", "params");
  const inputs = {};
  Object.entries(st.params).forEach(([key, val]) => {
    const f = el("div", "field");
    f.append(el("label", "", key));
    const inp = el("input");
    const numeric = typeof val === "number";
    inp.type = numeric ? "number" : "text";
    if (numeric && !Number.isInteger(val)) inp.step = "0.01";
    inp.value = val;
    f.append(inp);
    const dflt = st.defaults[key];
    if (dflt !== undefined) f.append(el("span", "dflt", "پیش‌فرض: " + dflt));
    inp.addEventListener("input", () =>
      f.classList.toggle("changed", String(inp.value) !== String(val))
    );
    inputs[key] = { inp, original: val, numeric };
    params.append(f);
  });
  card.append(params);

  const actions = el("div", "card-actions");
  const save = el("button", "btn btn-primary btn-sm", "ذخیره پارامترها");
  const note = el("span", "note");
  actions.append(save, note);
  card.append(actions);

  save.addEventListener("click", async () => {
    const patch = {};
    let bad = null;
    for (const [key, { inp, original, numeric }] of Object.entries(inputs)) {
      if (String(inp.value) === String(original)) continue;
      if (numeric) {
        const n = Number(inp.value);
        if (!Number.isFinite(n)) { bad = key; break; }
        patch[key] = n;
      } else {
        patch[key] = inp.value;
      }
    }
    if (bad) { note.className = "note bad"; note.textContent = `مقدار «${bad}» عدد نیست.`; return; }
    if (!Object.keys(patch).length) { note.className = "note"; note.textContent = "تغییری نبود."; return; }

    save.disabled = true;
    note.className = "note";
    note.textContent = "در حال ذخیره…";
    try {
      await api("/api/strategies/" + encodeURIComponent(st.name), {
        method: "PUT",
        body: JSON.stringify({ params: patch }),
      });
      note.className = "note ok";
      note.textContent = "ذخیره شد؛ از پاس بعدی اعمال می‌شود.";
      toast(`پارامترهای «${st.name}» ذخیره شد.`, "ok");
      loadStrategies();
    } catch (err) {
      note.className = "note bad";
      note.textContent = err.message;
    } finally {
      save.disabled = false;
    }
  });

  return card;
}

// ------------------------------------------------------------------ symbols
let watched = new Set();

async function loadSymbols() {
  // این درخواست کل بازار را می‌گیرد و چند ثانیه طول می‌کشد. بدون این حالت
  // انتظار، تب چند ثانیه کاملاً خالی می‌ماند و شکسته به نظر می‌رسد.
  const avail = $("#available");
  avail.innerHTML = "";
  avail.append(el("span", "note", "در حال گرفتن لیست بازار…"));

  try {
    const d = await api("/api/symbols");
    watched = new Set(d.watched || []);
    renderWatched();

    avail.innerHTML = "";
    $("#available-count").textContent = d.available.length ? `(${d.available.length})` : "";

    if (d.error) {
      avail.append(
        el("div", "error", "لیست بازار در دسترس نیست (بازار بسته یا شبکه قطع): " + d.error)
      );
      return;
    }
    renderAvailable(d.available);
    $("#symbol-search").oninput = () => renderAvailable(d.available);
  } catch (err) {
    avail.innerHTML = "";
    avail.append(el("div", "error", "خطا: " + err.message));
    $("#watched").innerHTML = "";
    $("#watched").append(el("div", "error", "خطا: " + err.message));
  }
}

function renderWatched() {
  const box = $("#watched");
  box.innerHTML = "";
  $("#watched-count").textContent = watched.size ? `(${watched.size})` : "";
  if (!watched.size) {
    box.append(el("span", "note", "هیچ نمادی انتخاب نشده."));
    return;
  }
  [...watched].sort().forEach((s) => {
    const chip = el("span", "sym on");
    chip.append(document.createTextNode(s));
    chip.append(el("span", "x", "×"));
    chip.title = "حذف از لیست رصد";
    chip.onclick = () => { watched.delete(s); renderWatched(); };
    box.append(chip);
  });
}

function renderAvailable(list) {
  const q = ($("#symbol-search").value || "").trim();
  const box = $("#available");
  box.innerHTML = "";
  const shown = q ? list.filter((s) => s.includes(q)) : list;
  if (!shown.length) {
    box.append(el("span", "note", "نمادی پیدا نشد."));
    return;
  }
  shown.forEach((s) => {
    const chip = el("span", "sym" + (watched.has(s) ? " on" : ""), s);
    chip.title = watched.has(s) ? "در لیست رصد است" : "افزودن به لیست رصد";
    chip.onclick = () => {
      watched.has(s) ? watched.delete(s) : watched.add(s);
      renderWatched();
      renderAvailable(list);
    };
    box.append(chip);
  });
}

$("#btn-save-symbols").addEventListener("click", async () => {
  const note = $("#symbols-note");
  if (!watched.size) {
    note.className = "note bad";
    note.textContent = "حداقل یک نماد لازم است.";
    return;
  }
  note.className = "note";
  note.textContent = "در حال ذخیره…";
  try {
    await api("/api/symbols", {
      method: "PUT",
      body: JSON.stringify({ symbols: [...watched] }),
    });
    note.className = "note ok";
    note.textContent = `${watched.size} نماد ذخیره شد.`;
    toast("لیست نمادها ذخیره شد.", "ok");
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

// ------------------------------------------------------------------ risk
const RISK_LABELS = {
  account_equity: "دارایی حساب (ریال)",
  risk_per_trade_pct: "درصد ریسک در هر معامله",
  max_position_pct: "سقف درصد دارایی در یک پوزیشن",
  max_contracts: "سقف تعداد قرارداد",
  stop_loss_pct: "درصد حد ضرر",
  take_profit_pct: "درصد حد سود",
};

async function loadRisk() {
  const box = $("#risk-fields");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  try {
    const risk = await api("/api/risk");
    box.innerHTML = "";
    Object.entries(RISK_LABELS).forEach(([key, label]) => {
      if (!(key in risk)) return;
      const f = el("div", "field");
      f.style.marginBottom = "11px";
      f.append(el("label", "", label));
      const inp = el("input");
      inp.type = "number";
      inp.step = key === "max_contracts" || key === "account_equity" ? "1" : "0.1";
      inp.value = risk[key];
      inp.dataset.key = key;
      inp.dataset.original = risk[key];
      inp.addEventListener("input", () =>
        f.classList.toggle("changed", inp.value !== inp.dataset.original)
      );
      f.append(inp);
      box.append(f);
    });

    // دارایی از کارگزاری: چک‌باکس، نه عدد — پس جدا ساخته می‌شود
    const cb = el("div", "field");
    const lbl = el("label");
    const box2 = el("input");
    box2.type = "checkbox";
    box2.id = "risk-use-broker";
    box2.checked = risk.use_broker_equity === true;
    box2.dataset.original = String(box2.checked);
    box2.addEventListener("change", () =>
      cb.classList.toggle("changed", String(box2.checked) !== box2.dataset.original)
    );
    lbl.append(box2);
    lbl.append(document.createTextNode(" دارایی حساب را از کارگزاری بخوان"));
    cb.append(lbl);
    cb.append(el("div", "note",
      "عدد بالا سریع کهنه می‌شود. نیاز به فعال بودن اتصال کارگزاری دارد؛ " +
      "اگر موجودی خوانده نشود، همان عدد بالا استفاده می‌شود."));
    box.append(cb);
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

$("#btn-save-risk").addEventListener("click", async () => {
  const note = $("#risk-note");
  const patch = {};
  document.querySelectorAll("#risk-fields input[type=number]").forEach((inp) => {
    if (inp.value !== inp.dataset.original) patch[inp.dataset.key] = Number(inp.value);
  });
  // چک‌باکس باید boolean برود، نه ۰/۱ — وگرنه pydantic آن را رد می‌کند
  const useBroker = $("#risk-use-broker");
  if (useBroker && String(useBroker.checked) !== useBroker.dataset.original) {
    patch.use_broker_equity = useBroker.checked;
  }
  if (!Object.keys(patch).length) {
    note.className = "note";
    note.textContent = "تغییری نبود.";
    return;
  }
  note.className = "note";
  note.textContent = "در حال ذخیره…";
  try {
    await api("/api/risk", { method: "PUT", body: JSON.stringify(patch) });
    note.className = "note ok";
    note.textContent = "ذخیره شد؛ از پاس بعدی اعمال می‌شود.";
    toast("تنظیمات ریسک ذخیره شد.", "ok");
    loadRisk();
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

// ------------------------------------------------------------------ account
// کارت موجودی. بورس تهران تسویه T+۰/T+۱/T+۲ دارد، پس «موجودی» سه عدد است.
function balanceCard(b, useBrokerEquity) {
  const card = el("div", "card");
  card.append(el("h3", "", "موجودی حساب"));

  const grid = el("div", "sig-grid");
  const cell = (k, v, cls) => {
    const dv = el("div");
    dv.append(el("span", "k", k));
    dv.append(el("span", "v " + (cls || ""), v));
    return dv;
  };
  grid.append(cell("دارایی مبنای ریسک", fmt(b.equity), "v-gain"));
  grid.append(cell("قدرت خرید T+۰", fmt(b.buy_power_t0)));
  grid.append(cell("قدرت خرید T+۲", fmt(b.buy_power_t2)));
  grid.append(cell("نقد T+۰", fmt(b.cash_t0)));
  grid.append(cell("نقد T+۲", fmt(b.cash_t2)));
  if (b.margin_blocked) grid.append(cell("وجه تضمین بلوکه", fmt(b.margin_blocked), "v-loss"));
  if (b.blocked) grid.append(cell("بلوکه‌شده", fmt(b.blocked), "v-loss"));
  if (b.credit) grid.append(cell("اعتبار", fmt(b.credit)));
  card.append(grid);

  card.append(el("div", "note", useBrokerEquity
    ? "اندازه‌گیری ریسک روی همین عدد انجام می‌شود."
    : "برای استفاده از این عدد در اندازه‌گیری ریسک، در تب «ریسک» گزینه‌ی " +
      "«دارایی حساب را از کارگزاری بخوان» را روشن کنید."));
  return card;
}

async function loadAccount() {
  const box = $("#account");
  box.innerHTML = '<p class="empty">در حال خواندن حساب…</p>';
  try {
    const d = await api("/api/account");
    box.innerHTML = "";

    if (!d.enabled) {
      box.append(
        el("div", "hint",
          "اتصال به حساب کارگزاری خاموش است. برای روشن کردن، در " +
          "config/settings.yaml مقدار broker.enabled را true بگذارید و " +
          "با 3-discover-api.bat یک بار لاگین کنید.")
      );
      return;
    }
    if (d.reason) {
      box.append(el("div", "error", d.reason));
      return;
    }

    // موجودی قبل از پوزیشن‌ها می‌آید — حساب بدون پوزیشن هم موجودی دارد
    if (d.balance) box.append(balanceCard(d.balance, d.use_broker_equity));

    if (!d.positions.length) {
      box.append(el("p", "empty", "پوزیشن باز آپشنی ندارید."));
      return;
    }

    d.positions.forEach((p) => {
      const card = el("div", "sig " + (p.is_long ? "call" : "put"));
      const head = el("div", "sig-head");
      head.append(el("span", "sig-badge " + (p.is_long ? "badge-call" : "badge-put"),
        p.is_long ? "خرید" : "فروش"));
      head.append(el("span", "sig-sym", p.symbol_name || p.symbol_isin));
      if (p.cash_settlement_date) {
        head.append(el("span", "sig-time", "تسویه نقدی: " + p.cash_settlement_date));
      }
      card.append(head);

      const grid = el("div", "sig-grid");
      const cell = (k, v, cls) => {
        const dv = el("div");
        dv.append(el("span", "k", k));
        dv.append(el("span", "v " + (cls || ""), v));
        return dv;
      };
      grid.append(cell("تعداد", fmt(p.quantity) + " قرارداد"));
      grid.append(cell("قیمت اعمال", fmt(p.strike_price)));
      grid.append(cell("میانگین خرید", fmt(p.buy_average_price)));
      grid.append(cell("میانگین فروش", fmt(p.sell_average_price)));
      grid.append(cell("وجه تضمین", fmt(p.total_margin)));
      if (p.open_buy_quantity) grid.append(cell("سفارش خرید باز", fmt(p.open_buy_quantity)));
      if (p.open_sell_quantity) grid.append(cell("سفارش فروش باز", fmt(p.open_sell_quantity)));
      if (p.closed_pnl) {
        grid.append(cell("سود/زیان بسته‌شده", fmt(p.closed_pnl),
          p.closed_pnl >= 0 ? "v-gain" : "v-loss"));
      }
      card.append(grid);
      box.append(card);
    });
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

$("#btn-refresh-account").addEventListener("click", loadAccount);


// ------------------------------------------------------------------ report
const pct = (v) => (v === null || v === undefined ? "نامعلوم" : fmt(v * 100, 1) + "٪");
const pnl = (v) =>
  v === null || v === undefined ? "—" : (v >= 0 ? "+" : "") + fmt(v, 1) + "٪";

// معیارهای حرفه‌ای. `null` اینجا یعنی «نمونه کافی نبود» — نه صفر.
function metricsCard(m) {
  const card = el("div", "card");
  card.append(el("h3", "", "معیارهای عملکرد"));

  const num = (v, digits = 2, suffix = "") =>
    v === null || v === undefined ? "نامعلوم" : fmt(v, digits) + suffix;

  const rows = [
    ["انتظار ریاضی هر سیگنال", num(m.expectancy_pct, 2, "٪"),
     "مهم‌ترین عدد: نرخ برد بالا با زیان‌های بزرگ می‌تواند انتظار منفی بدهد."],
    ["میانه بازده", num(m.median_return_pct, 2, "٪"),
     "برخلاف میانگین، یک سیگنال پرت آن را جابه‌جا نمی‌کند."],
    ["میانگین برد / زیان",
     num(m.avg_win_pct, 2, "٪") + " / " + num(m.avg_loss_pct, 2, "٪"), ""],
    ["ضریب سود", num(m.profit_factor, 2),
     "مجموع بردها ÷ مجموع زیان‌ها. بیشتر از ۱ یعنی سودده."],
    ["شارپ (هر سیگنال)", num(m.sharpe_per_signal, 2),
     "سالانه‌سازی نشده؛ با شارپ سالانه‌ی جاهای دیگر مقایسه نکنید."],
    ["سورتینو (هر سیگنال)", num(m.sortino_per_signal, 2),
     "فقط نوسان سمت زیان را جریمه می‌کند."],
    ["حداکثر افت تجمعی", num(m.max_drawdown_pct, 2, "٪"),
     "میانگین مثبت، مسیر رسیدن به آن را پنهان می‌کند."],
    ["بلندترین زنجیره باخت", fmt(m.longest_losing_streak),
     "چند باخت پشت‌سرهم باید تحمل می‌کردید."],
    ["انحراف معیار", num(m.stdev_return_pct, 2, "٪"), ""],
  ];

  const table = buildTable(["معیار", "مقدار"], rows.map((r) => [r[0], r[1]]));
  // توضیح هر معیار روی همان سطر، تا معنایش گم نشود
  table.querySelectorAll("tbody tr").forEach((tr, i) => {
    if (rows[i] && rows[i][2]) tr.title = rows[i][2];
  });
  card.append(table);

  card.append(el("div", "note",
    "این اعداد روی سود/زیان واقعیِ سیگنال‌های ارزیابی‌شده حساب شده‌اند. " +
    "«نامعلوم» یعنی نمونه کافی نبود، نه صفر."));
  return card;
}

async function loadReport() {
  const box = $("#report");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  const win = $("#report-window").value;
  try {
    const d = await api("/api/report" + (win ? `?days=${win}` : ""));
    box.innerHTML = "";

    // --- خلاصه ---
    const s = d.summary;
    const cards = el("div", "stat-row");
    const stat = (label, value, cls) => {
      const c = el("div", "stat");
      c.append(el("div", "stat-v " + (cls || ""), value));
      c.append(el("div", "stat-k", label));
      return c;
    };
    cards.append(stat("کل سیگنال", fmt(s.total)));
    cards.append(stat("برد", fmt(s.wins), "v-gain"));
    cards.append(stat("باخت", fmt(s.losses), "v-loss"));
    cards.append(stat("در انتظار", fmt(s.pending)));
    cards.append(stat("نرخ برد", pct(s.win_rate)));
    cards.append(stat("میانگین سود", pnl(s.avg_pnl_pct),
      s.avg_pnl_pct >= 0 ? "v-gain" : "v-loss"));
    box.append(cards);

    if (s.pending === s.total && s.total > 0) {
      box.append(el("div", "hint",
        "هیچ سیگنالی هنوز ارزیابی نشده. دکمه «ارزیابی» را بزنید تا قیمت " +
        "فعلی از بازار خوانده و نتیجه ثبت شود."));
    }

    // --- معیارهای حرفه‌ای ---
    if (d.metrics && d.metrics.total > 0) box.append(metricsCard(d.metrics));

    // --- به تفکیک استراتژی ---
    if (d.by_strategy.length) {
      const card = el("div", "card");
      card.append(el("h3", "", "به تفکیک استراتژی"));
      card.append(buildTable(
        ["استراتژی", "کل", "برد", "باخت", "در انتظار", "نرخ برد", "میانگین", "بهترین", "بدترین"],
        d.by_strategy.map((r) => [
          r.strategy, fmt(r.total), fmt(r.wins), fmt(r.losses), fmt(r.pending),
          pct(r.win_rate), pnl(r.avg_pnl_pct), pnl(r.best_pnl_pct), pnl(r.worst_pnl_pct),
        ])));
      box.append(card);
    }

    // --- به تفکیک نماد ---
    if (d.by_underlying.length) {
      const card = el("div", "card");
      card.append(el("h3", "", "به تفکیک نماد پایه"));
      card.append(buildTable(
        ["نماد", "کل", "برد", "باخت", "میانگین سود"],
        d.by_underlying.map((r) => [
          r.underlying || "—", fmt(r.total), fmt(r.wins), fmt(r.losses), pnl(r.avg_pnl_pct),
        ])));
      box.append(card);
    }

    // --- سیگنال‌های اخیر ---
    if (d.recent.length) {
      const card = el("div", "card");
      card.append(el("h3", "", `سیگنال‌های اخیر (${d.recent.length})`));
      card.append(buildTable(
        ["تاریخ", "نماد", "استراتژی", "پرمیوم", "قیمت فعلی", "سود/زیان", "نتیجه"],
        d.recent.map((r) => [
          (r.created_at || "").slice(0, 16).replace("T", " "),
          r.symbol, r.strategy_name, fmt(r.suggested_price),
          r.price_at_check ? fmt(r.price_at_check) : "—",
          pnl(r.pnl_pct), outcomeLabel(r.outcome),
        ])));
      box.append(card);
    }
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

function outcomeLabel(o) {
  return { win: "برد", loss: "باخت", pending: "در انتظار",
           expired: "منقضی", unknown: "نامعلوم" }[o] || o;
}

function buildTable(headers, rows) {
  const wrap = el("div", "table-wrap");
  const t = el("table");
  const thead = el("thead");
  const hr = el("tr");
  headers.forEach((h) => hr.append(el("th", "", h)));
  thead.append(hr);
  t.append(thead);
  const tb = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr");
    row.forEach((cell) => {
      const td = el("td", "", String(cell));
      if (String(cell).startsWith("+")) td.className = "v-gain";
      else if (String(cell).startsWith("-")) td.className = "v-loss";
      tr.append(td);
    });
    tb.append(tr);
  });
  t.append(tb);
  wrap.append(t);
  return wrap;
}

$("#btn-refresh-report").addEventListener("click", loadReport);
$("#report-window").addEventListener("change", () => {
  const w = $("#report-window").value;
  $("#btn-export").href = "/api/report/export" + (w ? `?days=${w}` : "");
  loadReport();
});

$("#btn-evaluate").addEventListener("click", async () => {
  const btn = $("#btn-evaluate");
  const label = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>در حال خواندن قیمت‌ها…';
  $("#evaluate-result").innerHTML = "";
  try {
    const r = await api("/api/report/evaluate", { method: "POST" });
    $("#evaluate-result").append(el("div", "ok-box",
      `${fmt(r.evaluated)} سیگنال ارزیابی شد` +
      (r.skipped ? `، ${fmt(r.skipped)} رد شد (قیمت در دسترس نبود).` : ".")));
    await loadReport();
  } catch (err) {
    $("#evaluate-result").append(el("div", "error", "ارزیابی ناموفق بود: " + err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
});

// ------------------------------------------------------------------ broker setup
async function loadBrokerSetup() {
  try {
    const d = await api("/api/account");
    $("#broker-enabled").checked = !!d.enabled;
  } catch {
    /* تب حساب خودش خطا را نشان می‌دهد */
  }
}

$("#btn-save-broker").addEventListener("click", async () => {
  const note = $("#broker-note");
  const body = { enabled: $("#broker-enabled").checked };
  const token = $("#broker-token").value.trim();
  if (token) body.token = token;

  note.className = "note";
  note.textContent = "در حال ذخیره…";
  try {
    await api("/api/broker", { method: "PUT", body: JSON.stringify(body) });
    note.className = "note ok";
    note.textContent = "ذخیره شد.";
    $("#broker-token").value = "";  // توکن در فرم نمی‌ماند
    toast("تنظیمات کارگزاری ذخیره شد.", "ok");
    await loadAccount();
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

// ------------------------------------------------------------------ data source
async function loadDataSource() {
  try {
    const d = await api("/api/datasource");
    const fill = (sel, options, current) => {
      sel.innerHTML = "";
      options.forEach((o) => {
        const opt = el("option", "", o);
        opt.value = o;
        sel.append(opt);
      });
      sel.value = current;
    };
    fill($("#ds-market"), d.available_market_data, d.market_data_provider);
    fill($("#ds-chain"), d.available_option_chain, d.option_chain_provider);
    $("#ds-enrich").checked = d.enrich_with_broker;
    $("#ds-enrich").disabled = !d.broker_enabled;
    $("#ds-limit").value = d.enrich_limit;

    const note = $("#ds-note");
    if (!d.broker_enabled) {
      note.className = "note";
      note.textContent = "برای غنی‌سازی، اول در تب «حساب» اتصال کارگزاری را فعال کنید.";
    } else {
      note.textContent = "";
    }
  } catch (err) {
    $("#ds-note").className = "note bad";
    $("#ds-note").textContent = err.message;
  }
}

$("#btn-save-ds").addEventListener("click", async () => {
  const note = $("#ds-note");
  note.className = "note";
  note.textContent = "در حال ذخیره…";
  try {
    await api("/api/datasource", {
      method: "PUT",
      body: JSON.stringify({
        market_data_provider: $("#ds-market").value,
        option_chain_provider: $("#ds-chain").value,
        enrich_with_broker: $("#ds-enrich").checked,
        enrich_limit: Number($("#ds-limit").value),
      }),
    });
    note.className = "note ok";
    note.textContent = "ذخیره شد؛ از پاس بعدی اعمال می‌شود.";
    toast("منبع داده ذخیره شد.", "ok");
    loadStatus();
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

// نمایش خلاصه‌ی عمق: مهم‌ترین خبر این است که سفارش اصلاً پر می‌شود یا نه.
function depthLabel(depth) {
  if (!depth) return "—";
  if (!depth.fully_fillable) {
    return `فقط ${fmt(depth.filled_quantity)} از ${fmt(depth.available)} ⚠`;
  }
  const slip = depth.slippage === null || depth.slippage === undefined
    ? "" : ` (لغزش ${fmt(depth.slippage * 100, 2)}٪)`;
  return `${fmt(depth.available)} قرارداد${slip}`;
}

// ------------------------------------------------------------------ structures
// از /api/structures/kinds پر می‌شود تا با اسکنرهای واقعی هم‌گام بماند.
// فهرست دستی یعنی یک ساختار تازه بی‌صدا از UI جا می‌ماند.
const KIND_LABEL = {};

// `null` در ساختار هم‌سررسید یعنی **نامحدود** (لانگ کال)، ولی در
// ساختار چندسررسیدی یعنی **نامعلوم**. یکی گرفتنشان گمراه‌کننده است.
const money = (v, unknown = false) =>
  v === null || v === undefined ? (unknown ? "نامعلوم" : "نامحدود") : fmt(v);

async function initStructures() {
  // نمادها از همان لیست تحت رصد
  try {
    const d = await api("/api/symbols");
    const sel = $("#st-underlying");
    if (!sel.options.length) {
      (d.watched || []).forEach((s) => {
        const o = el("option", "", s);
        o.value = s;
        sel.append(o);
      });
    }
  } catch { /* تب خودش خطا را نشان می‌دهد */ }

  // ساختارها را از سرور بگیر و هم dropdown هم برچسب‌ها را پر کن
  try {
    const d = await api("/api/structures/kinds");
    const sel = $("#st-kind");
    const kinds = d.kinds || [];
    kinds.forEach((k) => { KIND_LABEL[k.key] = k.label; });

    // فقط یک بار: گزینه‌ی «همه» می‌ماند و بقیه از سرور می‌آید
    if (sel.options.length <= 1) {
      kinds.forEach((k) => {
        const o = el("option", "", k.label);
        o.value = k.key;
        sel.append(o);
      });
    }
  } catch { /* dropdown با گزینه‌ی «همه» کار می‌کند */ }

  try {
    const d = await api("/api/structures/rank-keys");
    const sel = $("#st-rank");
    if (!sel.options.length) {
      const labels = {
        roi: "ROI", max_profit: "حداکثر سود", max_loss: "حداکثر زیان",
        risk_reward: "ریسک/ریوارد", liquidity_score: "نقدشوندگی",
        min_open_interest: "موقعیت باز", max_relative_spread: "اسپرد",
        distance_to_breakeven: "فاصله تا سربه‌سر", net_credit: "اعتبار خالص",
        required_capital: "سرمایه لازم",
      };
      d.keys.forEach((k) => {
        const o = el("option", "", labels[k.key] || k.key);
        o.value = k.key;
        sel.append(o);
      });
      sel.value = "roi";
    }
  } catch { /* ignore */ }
}

$("#btn-scan-structures").addEventListener("click", async () => {
  const box = $("#structures");
  const btn = $("#btn-scan-structures");
  const underlying = $("#st-underlying").value;
  if (!underlying) {
    box.innerHTML = "";
    box.append(el("div", "error", "اول یک نماد انتخاب کنید."));
    return;
  }

  const label = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="spin"></span>در حال اسکن…';
  box.innerHTML = '<p class="empty">در حال خواندن زنجیره اختیار…</p>';

  try {
    const params = new URLSearchParams({
      underlying,
      kind: $("#st-kind").value,
      rank_by: $("#st-rank").value,
      min_open_interest: $("#st-oi").value || "50",
      limit: "8",
      with_depth: $("#st-depth").checked ? "true" : "false",
    });
    const d = await api("/api/structures?" + params);
    box.innerHTML = "";

    const head = el("div", "hint");
    head.textContent =
      `${d.underlying} @ ${fmt(d.spot_price)} — مرتب بر اساس ${$("#st-rank").selectedOptions[0].text}`;
    box.append(head);

    let total = 0;
    Object.entries(d.structures).forEach(([kind, items]) => {
      if (!items.length) return;
      total += items.length;
      const section = el("div", "card");
      section.append(el("h3", "", `${KIND_LABEL[kind] || kind} (${items.length})`));
      items.forEach((s) => section.append(structureCard(s)));
      box.append(section);
    });

    if (!total) {
      box.append(el("p", "empty",
        "ساختار معتبری پیدا نشد. شاید حداقل موقعیت باز را کم کنید."));
    }
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "اسکن ناموفق بود: " + err.message));
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
});

function structureCard(s) {
  const card = el("div", "sig");
  card.style.marginTop = "10px";

  const head = el("div", "sig-head");
  head.append(el("span", "sig-sym", KIND_LABEL[s.strategy_type] || s.strategy_type));
  if (s.expiration) head.append(el("span", "sig-strategy", "سررسید " + s.expiration));
  if (s.has_leg_risk) {
    const warn = el("span", "chip chip-warn", "ریسک اجرای ناقص");
    warn.title = "کارگزاری سفارش چندپایه اتمیک ندارد؛ پایه‌ها جدا ثبت می‌شوند.";
    head.append(warn);
  }
  if (s.single_expiry === false) {
    const warn = el("span", "chip chip-warn", "سود تقریبی نیست — نامعلوم");
    warn.title = s.approximation_note ||
      "دو سررسید دارد؛ منحنی سود در سررسید برایش معنا ندارد.";
    head.append(warn);
  }
  card.append(head);

  const grid = el("div", "sig-grid");
  const cell = (k, v, cls) => {
    const d = el("div");
    d.append(el("span", "k", k));
    d.append(el("span", "v " + (cls || ""), v));
    return d;
  };
  const unknown = s.single_expiry === false;
  grid.append(cell("حداکثر سود", money(s.max_profit, unknown), "v-gain"));
  grid.append(cell("حداکثر زیان", money(s.max_loss, unknown), "v-loss"));
  grid.append(cell("ROI", s.roi !== null ? fmt(s.roi * 100, 1) + "٪" : "—"));
  grid.append(cell("ریسک/ریوارد", s.risk_reward !== null ? fmt(s.risk_reward, 2) : "—"));
  grid.append(cell("سرمایه لازم", money(s.required_capital)));
  grid.append(cell("سربه‌سر", (s.breakevens || []).map((b) => fmt(b)).join(" — ") || "—"));
  if (s.net_credit) grid.append(cell("اعتبار خالص", fmt(s.net_credit), "v-gain"));
  if (s.liquidity_score !== null) {
    grid.append(cell("نقدشوندگی", fmt(s.liquidity_score * 100, 0) + "٪"));
  }
  grid.append(cell("موقعیت باز", fmt(s.min_open_interest)));
  card.append(grid);

  // نقشه سفارش
  const plan = el("div", "table-wrap");
  plan.style.marginTop = "8px";
  // ستون عمق فقط وقتی می‌آید که واقعاً خوانده شده باشد
  const hasDepth = (s.legs || []).some((leg) => leg.depth);
  const rows = (s.legs || []).map((leg) => {
    const row = [
      leg.action === "BUY" ? "خرید" : "فروش",
      leg.instrument === "UNDERLYING" ? "سهم پایه" : leg.instrument,
      leg.strike !== null && leg.strike !== undefined ? fmt(leg.strike) : "—",
      fmt(leg.quantity),
      fmt(leg.limit_price),
      leg.role || "",
    ];
    if (hasDepth) row.push(depthLabel(leg.depth));
    return row;
  });
  const headers = ["عمل", "ابزار", "استرایک", "تعداد", "قیمت حد", "نقش"];
  if (hasDepth) headers.push("عمق / پر شدن");
  plan.append(buildTable(headers, rows));
  card.append(plan);
  return card;
}

// ------------------------------------------------------------------ paper trading
async function loadPaperSettings() {
  try {
    const d = await api("/api/paper-trading/settings");
    $("#paper-enabled").checked = !!d.enabled;
    $("#paper-initial-balance").value = d.initial_balance ?? "";
    const fees = d.fees || {};
    $("#paper-fee-buy").value = fees.buy_rate ?? 0;
    $("#paper-fee-sell").value = fees.sell_rate ?? 0;
    $("#paper-fee-tax").value = fees.sell_tax_rate ?? 0;
    $("#paper-fee-per-order").value = fees.per_order ?? 0;
  } catch (err) {
    const note = $("#paper-settings-note");
    note.className = "note bad";
    note.textContent = err.message;
  }
}

$("#btn-save-paper-settings").addEventListener("click", async () => {
  const note = $("#paper-settings-note");
  note.className = "note";
  note.textContent = "در حال ذخیره…";
  try {
    await api("/api/paper-trading/settings", {
      method: "PUT",
      body: JSON.stringify({
        enabled: $("#paper-enabled").checked,
        initial_balance: Number($("#paper-initial-balance").value),
        fees: {
          buy_rate: Number($("#paper-fee-buy").value),
          sell_rate: Number($("#paper-fee-sell").value),
          sell_tax_rate: Number($("#paper-fee-tax").value),
          per_order: Number($("#paper-fee-per-order").value),
        },
      }),
    });
    note.className = "note ok";
    note.textContent = "ذخیره شد.";
    toast("تنظیمات معاملات کاغذی ذخیره شد.", "ok");
    await refreshPaperTradingFlag();
    renderSignals();
    await loadPaperTab();
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

$("#btn-paper-reset").addEventListener("click", async () => {
  if (!confirm("حساب کاغذی کاملاً ریست شود؟ همه‌ی پوزیشن‌ها و تاریخچه پاک می‌شود.")) return;
  try {
    await api("/api/paper-trading/reset", { method: "POST" });
    toast("حساب کاغذی ریست شد.", "ok");
    await loadPaperTab();
  } catch (err) {
    toast("ریست ناموفق بود: " + err.message, "bad");
  }
});

function paperStat(label, value, sub, cls) {
  const c = el("div", "stat");
  c.append(el("div", "stat-v " + (cls || ""), value));
  c.append(el("div", "stat-k", label));
  if (sub) c.append(el("div", "stat-sub", sub));
  return c;
}

// علامتِ سود/زیان فقط وقتی معنا دارد که عدد **دانسته** باشد. برای
// `null` هیچ رنگی نمی‌گذاریم: رنگِ سبز روی «نامشخص» یعنی ادعای چیزی که
// نمی‌دانیم.
const signClass = (v) => (v === null || v === undefined ? "" : v >= 0 ? "v-gain" : "v-loss");

function paperCashRow(a) {
  const row = el("div", "stat-row");
  row.append(paperStat("سرمایه اولیه", fmt(a.initial_balance)));
  row.append(paperStat("وجه نقد", fmt(a.cash)));
  row.append(paperStat("مبلغ مسدود", fmt(a.blocked), "سفارش معلق و وجه تضمین مدل نشده"));
  row.append(paperStat("وجه قابل استفاده", fmt(a.available)));
  return row;
}

function paperValueRow(a) {
  const row = el("div", "stat-row");

  // ارزش روز: اگر حتی یک موقعیت قیمت نخورده باشد «—» است، نه عددی که
  // بخشی از دارایی را جا انداخته.
  row.append(paperStat(
    "ارزش روز موقعیت‌ها",
    fmt(a.market_value),
    a.valuation_complete ? null : `قیمت‌خورده: ${fmt(a.market_value_priced)}`,
  ));

  // «خالص» فقط وقتی عدد می‌گیرد که هزینه‌های همان عملیات دانسته باشند.
  // `fmt(null)` همان «—» است، و زیرنویس می‌گوید چرا.
  row.append(paperStat(
    "سود/زیان تحقق‌یافته",
    fmt(a.realized_net),
    a.realized_net === null
      ? `ناخالص ${fmt(a.realized_gross)} · هزینه‌ی ثبت‌شده ${fmt(a.realized_costs)} (ناقص)`
      : `ناخالص ${fmt(a.realized_gross)} − هزینه ${fmt(a.realized_costs)}`,
    signClass(a.realized_net),
  ));

  const unrealizedSub = () => {
    if (a.unrealized_gross === null) return "ارزش‌گذاری ناقص";
    if (a.unrealized_net === null) {
      return `ناخالص ${fmt(a.unrealized_gross)} · کارمزد ورود نامعلوم`;
    }
    return `ناخالص ${fmt(a.unrealized_gross)} − کارمزد ورود ${fmt(a.open_entry_costs)}`;
  };
  row.append(paperStat(
    "سود/زیان تحقق‌نیافته",
    fmt(a.unrealized_net),
    unrealizedSub(),
    signClass(a.unrealized_net),
  ));

  row.append(paperStat(
    "ارزش کل حساب",
    fmt(a.equity),
    a.equity === null
      ? `دست‌کم ${fmt(a.equity_priced_part)}`
      : `بازده ${a.total_return_pct === null ? "—" : a.total_return_pct.toFixed(2) + "٪"}`,
    a.equity === null ? "" : signClass(a.equity - a.initial_balance),
  ));
  return row;
}

/** نوارهای وضعیت — هر چیزی که عدد بالا را مشروط می‌کند، صریح گفته شود. */
function paperNotices(a) {
  const box = el("div", "paper-notices");

  if (!a.valuation_complete) {
    const names = (a.unpriced || [])
      .map((p) => `${p.symbol} (${p.status_label})`)
      .join("، ");
    box.append(el(
      "div", "warnbar",
      `ارزش‌گذاری ناقص: ${fmt(a.unpriced_count)} موقعیت قیمت نخورده — ${names}. ` +
      "«ارزش کل حساب» تا روشن شدن قیمت نامشخص می‌ماند و با صفر پر نمی‌شود.",
    ));
  }

  if (!a.rates_configured) {
    box.append(el(
      "div", "warnbar",
      "نرخ کارمزد/مالیات وارد نشده و برای معامله‌های بعدی صفر فرض می‌شود. " +
      "نرخ خودتان را در تنظیمات بالا وارد کنید تا شبیه‌سازی واقعی‌تر شود. " +
      "(این تنظیم روی معامله‌های گذشته اثری ندارد.)",
    ));
  }

  if (!a.costs_known) {
    const parts = [];
    if (a.trades_missing_entry_cost > 0) {
      parts.push(`${fmt(a.trades_missing_entry_cost)} معامله‌ی بسته‌شده`);
    }
    if ((a.positions_with_unknown_cost || []).length) {
      parts.push(`موقعیت ${a.positions_with_unknown_cost.join("، ")}`);
    }
    box.append(el(
      "div", "warnbar",
      `هزینه‌ی ${parts.join(" و ")} دانسته نیست (ثبت‌شده پیش از تفکیک ` +
      "هزینه‌ها). تا آن موقع «خالص» به‌عنوان عدد قطعی نمایش داده نمی‌شود؛ " +
      "ناخالص و هزینه‌های ثبت‌شده همچنان درست‌اند.",
    ));
  }

  const rec = a.reconciliation || {};
  if (rec.applicable && rec.ok === false) {
    box.append(el(
      "div", "error",
      `تطبیق حساب نخواند (اختلاف ${fmt(rec.difference, 2)} ریال). ` +
      "به اعداد این صفحه تکیه نکنید تا علتش پیدا شود.",
    ));
  }
  return box;
}

function paperStatRow(a) {
  const box = el("div");
  box.append(paperCashRow(a));
  box.append(paperValueRow(a));
  box.append(paperNotices(a));
  if (a.priced_at) {
    box.append(el("p", "note", `قیمت‌گذاری در ${a.priced_at.replace("T", " ")}`));
  }
  return box;
}

async function loadPaperAccount() {
  const box = $("#paper-account");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  try {
    const a = await api("/api/paper-trading/account");
    box.innerHTML = "";
    box.append(paperStatRow(a));
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

async function loadPaperPositions() {
  const box = $("#paper-positions");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  try {
    const d = await api("/api/paper-trading/positions");
    box.innerHTML = "";
    if (!d.positions.length) {
      box.append(el("p", "empty", "پوزیشن باز کاغذی ندارید."));
      return;
    }
    // «قیمت خروج» و «ارزش روز» برای موقعیتِ قیمت‌نخورده «—» می‌مانند.
    // ستون «وضعیت» می‌گوید چرا — تا خالی بودن با صفر اشتباه نشود.
    const rows = d.positions.map((p) => [
      p.symbol,
      fmt(p.quantity),
      fmt(p.average_price),
      p.status_label,
      fmt(p.mark_price),
      fmt(p.market_value),
      fmt(p.unrealized_gross),
      fmt(p.unrealized_net),
      "",
    ]);
    const table = buildTable(
      ["نماد", "تعداد", "میانگین خرید", "وضعیت", "قیمت خروج", "ارزش روز",
       "شناور ناخالص", "شناور خالص", ""],
      rows,
    );
    table.querySelectorAll("tbody tr").forEach((tr, i) => {
      const position = d.positions[i];
      const cells = tr.children;
      if (position.status !== "ok") cells[3].classList.add("v-loss");
      // `signClass` برای مقدار نامشخص رشته‌ی خالی می‌دهد و `classList.add("")`
      // استثنا پرتاب می‌کند — دقیقاً همان حالتی که این صفحه باید تابش بیاورد.
      const paint = (cell, value) => {
        const cls = signClass(value);
        if (cls) cell.classList.add(cls);
      };
      paint(cells[6], position.unrealized_gross);
      paint(cells[7], position.unrealized_net);
      if (position.status === "partial_depth" && position.reference_price !== null) {
        cells[4].textContent = `${fmt(position.reference_price)} (مرجع)`;
      }
      const lastCell = tr.lastElementChild;
      lastCell.textContent = "";
      if (position.status === "expired_unsettled") {
        const btn = el("button", "btn btn-ghost", "تسویه");
        btn.addEventListener("click", () => settlePaperPosition(position, btn));
        lastCell.append(btn);
      } else {
        const btn = el("button", "btn btn-ghost", "بستن پوزیشن");
        btn.addEventListener("click", () => closePaperPosition(position, btn));
        lastCell.append(btn);
      }
    });
    box.append(table);
    if (d.expired_unsettled.length) {
      box.append(el(
        "div", "warnbar",
        `${d.expired_unsettled.join("، ")} سررسید شده و تسویه نشده است. ` +
        "خودکار با آخرین قیمت معامله‌شده تسویه نمی‌شود؛ قیمت تسویه را " +
        "خودتان بدهید.",
      ));
    }
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

async function settlePaperPosition(position, btn) {
  const total = (p) => fmt(p * position.quantity * position.contract_size);
  const raw = prompt(
    [
      `پرمیوم تسویه‌ی ${position.symbol} به ازای هر واحد؟`,
      `ارزش کل = قیمت × ${position.quantity} × ${position.contract_size}`,
      "این فرضِ شماست، نه تسویه‌ی رسمی: قواعد اعمال بورس و هزینه‌ی خودِ",
      "تسویه در این شبیه‌ساز پیاده نشده‌اند.",
      "صفر معتبر است (انقضای بی‌ارزش) ولی باید صریح وارد شود.",
    ].join("\n"),
    "",
  );
  if (raw === null) return;
  // ⚠️ `Number("")` و `Number("   ")` هر دو صفر می‌دهند. بدون این بررسی،
  // زدنِ OK روی فیلد خالی به «تسویه با قیمت صفر» تعبیر می‌شد — یعنی حساب
  // موقعیت را بی‌ارزش می‌بست بی‌آنکه کاربر چنین گفته باشد.
  if (raw.trim() === "") {
    toast("قیمت تسویه وارد نشد. صفر باید صریح نوشته شود.", "bad");
    return;
  }
  const price = Number(raw);
  if (!Number.isFinite(price) || price < 0) {
    toast("قیمت تسویه باید یک عدد متناهیِ نامنفی باشد.", "bad");
    return;
  }
  if (!confirm(
    `تسویه‌ی ${position.symbol} با پرمیوم ${fmt(price)} هر واحد` +
    ` — ارزش کل ${total(price)} ریال.\nادامه؟`
  )) return;
  btn.disabled = true;
  try {
    await api("/api/paper-trading/settle", {
      method: "POST",
      body: JSON.stringify({ symbol: position.symbol, settlement_price: price }),
    });
    toast(`${position.symbol} تسویه شد.`, "ok");
    await loadPaperTab();
  } catch (err) {
    toast("تسویه ناموفق بود: " + err.message, "bad");
    btn.disabled = false;
  }
}

async function closePaperPosition(position, btn) {
  btn.disabled = true;
  try {
    await api("/api/paper-trading/orders", {
      method: "POST",
      body: JSON.stringify({
        symbol: position.symbol,
        side: "sell",
        quantity: position.quantity,
      }),
    });
    toast(`پوزیشن ${position.symbol} بسته شد.`, "ok");
    await loadPaperTab();
  } catch (err) {
    toast("بستن پوزیشن ناموفق بود: " + err.message, "bad");
    btn.disabled = false;
  }
}

async function loadPaperOrders() {
  const box = $("#paper-orders");
  box.innerHTML = '<p class="empty">در حال بارگذاری…</p>';
  try {
    const d = await api("/api/paper-trading/orders");
    box.innerHTML = "";
    if (!d.orders.length) {
      box.append(el("p", "empty", "هنوز سفارش کاغذی ثبت نشده."));
      return;
    }
    const rows = d.orders.map((o) => [
      o.created_at ? new Date(o.created_at).toLocaleString("fa-IR") : "",
      o.symbol,
      o.side === "buy" ? "خرید" : "فروش",
      fmt(o.quantity),
      fmt(o.filled_quantity),
      fmt(o.avg_fill_price),
      o.status,
    ]);
    box.append(buildTable(
      ["زمان", "نماد", "سمت", "تعداد", "پرشده", "قیمت پرشدن", "وضعیت"], rows
    ));
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

$("#btn-refresh-paper-orders").addEventListener("click", loadPaperOrders);

// ------------------------------------------------------------------ paper order form: chain filter bar + table
const OPTION_TYPE_LABEL = { call: "کال", put: "پوت" };
//: زنجیره‌ی کامل نماد پایه‌ی فعلی — فیلترهای سررسید/نوع روی همین کش عمل می‌کنند
let paperChainContracts = [];

async function loadPaperChainUnderlyings() {
  const sel = $("#paper-chain-underlying");
  if (sel.options.length) return;  // فقط یک‌بار پر می‌شود
  try {
    const d = await api("/api/symbols");
    (d.watched || []).forEach((s) => {
      const o = el("option", "", s);
      o.value = s;
      sel.append(o);
    });
    // پرکردن اولیه: مرورگر خودش اولین گزینه را انتخاب می‌کند ولی
    // رویداد change شلیک نمی‌شود، پس زنجیره‌اش را دستی بار می‌کنیم
    if (sel.value) await loadPaperChain();
  } catch { /* دراپ‌داون خالی می‌ماند؛ کاربر پیام خطای زنجیره را می‌بیند */ }
}

function selectPaperContract(symbol) {
  $("#paper-order-symbol").value = symbol;
  $("#paper-chain-table").querySelectorAll("tbody tr").forEach((tr) => {
    tr.classList.toggle("selected", tr.dataset.symbol === symbol);
  });
}

function renderPaperChainTable() {
  const box = $("#paper-chain-table");
  box.innerHTML = "";

  const expiry = $("#paper-chain-expiry").value;
  const type = $("#paper-chain-type").value;
  const filtered = paperChainContracts.filter(
    (c) => (!expiry || c.expiry === expiry) && (!type || c.option_type === type)
  );

  if (!filtered.length) {
    box.append(el("p", "empty", "با این فیلتر قراردادی نیست."));
    return;
  }

  const rows = filtered.map((c) => [
    c.symbol, OPTION_TYPE_LABEL[c.option_type] || c.option_type, fmt(c.strike), c.expiry,
  ]);
  const table = buildTable(["نماد", "نوع", "قیمت اعمال", "سررسید"], rows);
  table.querySelectorAll("tbody tr").forEach((tr, i) => {
    tr.dataset.symbol = filtered[i].symbol;
    tr.classList.add("clickable");
    tr.addEventListener("click", () => selectPaperContract(filtered[i].symbol));
  });
  box.append(table);
  // انتخاب فعلی (اگر هنوز در نتیجه‌ی فیلترشده باشد) را دوباره برجسته کن
  selectPaperContract($("#paper-order-symbol").value);
}

function fillPaperChainExpiries() {
  const sel = $("#paper-chain-expiry");
  const prev = sel.value;
  sel.innerHTML = "";
  const allOption = el("option", "", "همه سررسیدها");
  allOption.value = "";
  sel.append(allOption);
  [...new Set(paperChainContracts.map((c) => c.expiry))].sort().forEach((e) => {
    const o = el("option", "", e);
    o.value = e;
    sel.append(o);
  });
  if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
}

async function loadPaperChain() {
  const underlying = $("#paper-chain-underlying").value;
  const box = $("#paper-chain-table");
  $("#paper-order-symbol").value = "";
  paperChainContracts = [];

  if (!underlying) {
    box.innerHTML = "";
    box.append(el("p", "empty", "اول نماد پایه را انتخاب کنید."));
    return;
  }

  box.innerHTML = '<p class="empty">در حال بارگذاری زنجیره…</p>';
  try {
    const d = await api("/api/paper-trading/chain?underlying=" + encodeURIComponent(underlying));
    paperChainContracts = d.contracts;
    fillPaperChainExpiries();
    renderPaperChainTable();
  } catch (err) {
    box.innerHTML = "";
    box.append(el("div", "error", "خطا: " + err.message));
  }
}

$("#paper-chain-underlying").addEventListener("change", loadPaperChain);
$("#paper-chain-expiry").addEventListener("change", renderPaperChainTable);
$("#paper-chain-type").addEventListener("change", renderPaperChainTable);

$("#btn-paper-order").addEventListener("click", async () => {
  const note = $("#paper-order-note");
  const symbol = $("#paper-order-symbol").value;
  const side = $("#paper-order-side").value;
  const quantity = Number($("#paper-order-qty").value);

  if (!symbol || !quantity) {
    note.className = "note bad";
    note.textContent = "یک قرارداد از جدول زنجیره انتخاب کنید و تعداد را وارد کنید.";
    return;
  }

  note.className = "note";
  note.textContent = "در حال ثبت سفارش…";
  try {
    const order = await api("/api/paper-trading/orders", {
      method: "POST",
      body: JSON.stringify({ symbol, side, quantity }),
    });
    note.className = order.status === "rejected" ? "note bad" : "note ok";
    note.textContent = order.status === "rejected"
      ? "رد شد: " + (order.metadata && order.metadata.reason)
      : `سفارش ${order.status === "filled" ? "کامل" : "بخشی"} پر شد @ ${fmt(order.price)}`;
    await loadPaperTab();
  } catch (err) {
    note.className = "note bad";
    note.textContent = err.message;
  }
});

async function executeSignalAsPaperOrder(signal, btn) {
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "در حال ثبت…";
  try {
    const order = await api("/api/paper-trading/orders", {
      method: "POST",
      body: JSON.stringify({ signal_id: signal.signal_id, quantity: signal.suggested_qty }),
    });
    if (order.status === "rejected") {
      toast("رد شد: " + (order.metadata && order.metadata.reason), "bad");
    } else {
      toast(`سیگنال با یک کلیک اجرا شد @ ${fmt(order.price)}`, "ok");
    }
  } catch (err) {
    toast("اجرای سیگنال ناموفق بود: " + err.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

async function loadPaperTab() {
  // موازی و مستقل: خطای یک بخش (مثلاً تنظیمات) نباید بقیه پنل را خالی نگه دارد
  await Promise.all([
    loadPaperSettings(),
    loadPaperAccount(),
    loadPaperPositions(),
    loadPaperOrders(),
    loadPaperChainUnderlyings(),
  ]);
}

// ------------------------------------------------------------------ boot
loadStatus();
refreshPaperTradingFlag().then(loadSignals);
setInterval(loadStatus, 60000);
