/* 采集表付费下载：共享支付弹层（工具页 / 落地页 / 碳核算导出共用）。
 *
 * 用法：
 *   const code = await ESGPay.ensure({ productId: "esg-template", source: "app" });
 *   if (!code) return;                  // 用户关闭或未解锁
 *   fetch("/client/template?code=" + encodeURIComponent(code));
 *
 * 授权码保存在 localStorage（esg_license）；管理员口令通过 ?token= 传入时不落盘。
 */
(function () {
  "use strict";

  var LS_KEY = "esg_license";
  var DEFAULT_PRODUCT = "esg-template";
  var WA = "https://wa.me/85270135587?text=" + encodeURIComponent("你好，我想购买非上市企业 ESG 数据采集表");
  var MAIL = "mailto:webmarinda@gmail.com?subject=" + encodeURIComponent("购买 ESG 采集表授权");
  var POLL_MS = 2500;
  var MAX_POLLS = 240;

  var configPromise = null;
  var stylesReady = false;
  var overlay = null;
  var state = { product: null, order: null, timer: null, polls: 0, resolve: null };

  function track(name, params) {
    if (window.gtag) window.gtag("event", name, params || {});
  }

  function getConfig() {
    if (!configPromise) {
      configPromise = fetch("/api/pay/config", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .catch(function () { return { pay_enabled: false, products: [] }; });
    }
    return configPromise;
  }

  function yuan(v) { return "¥" + Number(v).toFixed(2); }

  function verify(code) {
    return fetch("/api/entitle/check?scope=template&code=" + encodeURIComponent(code), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { return Boolean(d && d.entitled); })
      .catch(function () { return false; });
  }

  function storedLicense() {
    try { return localStorage.getItem(LS_KEY) || ""; } catch (_) { return ""; }
  }
  function setLicense(code) {
    try { localStorage.setItem(LS_KEY, code); } catch (_) { /* 隐私模式忽略 */ }
  }
  function clearLicense() {
    try { localStorage.removeItem(LS_KEY); } catch (_) { /* ignore */ }
  }

  function injectStyles() {
    if (stylesReady) return;
    stylesReady = true;
    var css = [
      ".esgpay-mask{position:fixed;inset:0;z-index:9000;background:rgba(12,20,16,.55);display:flex;align-items:center;justify-content:center;padding:16px}",
      ".esgpay-box{position:relative;width:100%;max-width:440px;max-height:92vh;overflow:auto;background:#fff;border-radius:14px;padding:22px 22px 18px;box-shadow:0 24px 60px rgba(0,0,0,.28);font-size:14px;line-height:1.7;color:#161A17;text-align:left}",
      ".esgpay-box h3{margin:0 24px 4px 0;font-size:19px}",
      ".esgpay-box p{margin:6px 0}",
      ".esgpay-sub{color:#6B726C;font-size:12.5px}",
      ".esgpay-price{margin:12px 0 6px;font-size:13px;color:#3c4a41}",
      ".esgpay-price b{font-size:26px;color:#0F5132;margin-right:4px}",
      ".esgpay-list{margin:8px 0 14px 18px;padding:0;color:#3c4a41;font-size:13px}",
      ".esgpay-list li{margin:3px 0}",
      ".esgpay-lite{margin:0 0 14px;padding:10px 12px;background:#F6F8F5;border:1px dashed #C9DCD2;border-radius:8px;font-size:12.5px;color:#3c4a41;line-height:1.6}",
      ".esgpay-lite a{color:#0F5132;font-weight:700;margin-left:2px}",
      ".esgpay-btn{display:inline-block;width:100%;padding:11px 16px;border:0;border-radius:8px;background:#0F5132;color:#fff;font-weight:700;font-size:14px;cursor:pointer;font-family:inherit}",
      ".esgpay-btn:hover{background:#1F6B4C}",
      ".esgpay-btn[disabled]{opacity:.6;cursor:default}",
      ".esgpay-ghost{background:#fff;color:#0F5132;border:1px solid #0F5132}",
      ".esgpay-x{position:absolute;top:10px;right:12px;border:0;background:transparent;font-size:24px;line-height:1;color:#6B726C;cursor:pointer}",
      ".esgpay-qr{text-align:center;margin-top:12px;padding-top:12px;border-top:1px solid #E2E3D8}",
      ".esgpay-qr img{width:220px;height:220px;max-width:100%;background:#fff;border:1px solid #E2E3D8;border-radius:8px}",
      ".esgpay-status{margin-top:8px;font-weight:700;color:#0F5132}",
      ".esgpay-orderno{color:#6B726C;font-size:11.5px;word-break:break-all;margin-top:4px}",
      ".esgpay-done{margin-top:12px;padding:12px;background:#EFF5F1;border-radius:8px}",
      ".esgpay-code{display:flex;gap:8px;align-items:center;margin:8px 0 10px}",
      ".esgpay-code code{flex:1;display:block;padding:8px 10px;background:#fff;border:1px dashed #9FBFAC;border-radius:6px;font-size:12px;word-break:break-all}",
      ".esgpay-code button{white-space:nowrap;padding:8px 10px;border:1px solid #0F5132;background:#fff;color:#0F5132;border-radius:6px;cursor:pointer;font-family:inherit}",
      ".esgpay-manual{margin-top:14px;border-top:1px solid #E2E3D8;padding-top:10px}",
      ".esgpay-manual summary{cursor:pointer;color:#0F5132;font-weight:700;font-size:13px}",
      ".esgpay-row{display:flex;gap:8px;margin-top:8px}",
      ".esgpay-row input{flex:1;min-width:0;padding:9px 10px;border:1px solid #E2E3D8;border-radius:6px;font-family:inherit;font-size:13px}",
      ".esgpay-row button{white-space:nowrap;padding:9px 14px;border:0;border-radius:6px;background:#0F5132;color:#fff;font-weight:700;cursor:pointer;font-family:inherit}",
      ".esgpay-tip{color:#6B726C;font-size:12px}",
      ".esgpay-err{color:#A8542A;font-size:12.5px;min-height:18px;margin:6px 0 0}",
      ".esgpay-tip a{color:#0F5132}",
      "@media(max-width:480px){.esgpay-box{padding:18px 16px 14px}.esgpay-qr img{width:180px;height:180px}}"
    ].join("");
    var el = document.createElement("style");
    el.setAttribute("data-esgpay", "1");
    el.textContent = css;
    document.head.appendChild(el);
  }

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text != null) node.textContent = text;
    return node;
  }

  function buildModal() {
    injectStyles();
    var mask = el("div", { "class": "esgpay-mask" });
    var box = el("div", { "class": "esgpay-box", role: "dialog", "aria-modal": "true" });

    var close = el("button", { "class": "esgpay-x", type: "button", "aria-label": "关闭" }, "×");
    close.addEventListener("click", function () { finish(null); });

    var title = el("h3", null, "获取完整版 ESG 数据采集表");
    var sub = el("p", { "class": "esgpay-sub" }, "82 项 · 单企业授权 · 付款后立即解锁，可重复下载");

    var price = el("div", { "class": "esgpay-price" });
    price.innerHTML = '<b data-price>¥—</b><span>／企业 · 含 1 年版本更新</span>';

    var list = el("ul", { "class": "esgpay-list" });
    ["82 项：E24 / S24 / G16 + 行业特有 18 项", "含证据索引、行业对标分位、港府问卷 48 题", "完整版可上传诊断；精简版数据可直接迁移", "同一授权码可在其它设备再次解锁"].forEach(function (t) {
      list.appendChild(el("li", null, t));
    });

    var lite = el("div", { "class": "esgpay-lite" });
    lite.innerHTML = '<b>只想先试填？</b>免费精简版含 20 项核心指标（E9 / S6 / G5），同样可以上传诊断；' +
      '不含行业特有指标、证据索引与行业对标。<a href="/client/template-lite" download>免费下载精简版</a>';

    var payBox = el("div", { "class": "esgpay-pay" });
    var buy = el("button", { "class": "esgpay-btn", type: "button" }, "微信扫码支付");
    var payTip = el("p", { "class": "esgpay-tip" }, "支付成功后本页自动确认并显示授权码，无需等待人工。");
    payBox.appendChild(buy);
    payBox.appendChild(payTip);

    var qrBox = el("div", { "class": "esgpay-qr" });
    qrBox.hidden = true;
    var qrImg = el("img", { alt: "微信支付二维码" });
    var qrLink = el("a", { "class": "esgpay-tip", href: "#", target: "_blank", rel: "noopener" }, "在新窗口打开支付链接");
    var status = el("div", { "class": "esgpay-status" }, "等待支付…");
    var orderNo = el("div", { "class": "esgpay-orderno" });
    qrBox.appendChild(qrImg);
    qrBox.appendChild(status);
    qrBox.appendChild(orderNo);
    qrBox.appendChild(qrLink);

    var done = el("div", { "class": "esgpay-done" });
    done.hidden = true;
    var doneMsg = el("p", null, "支付成功，授权码已生成。请保存，可在任意设备解锁；点击下方按钮开始下载。");
    var codeRow = el("div", { "class": "esgpay-code" });
    var codeEl = el("code", null, "");
    var copyBtn = el("button", { type: "button" }, "复制");
    codeRow.appendChild(codeEl);
    codeRow.appendChild(copyBtn);
    var dlBtn = el("button", { "class": "esgpay-btn", type: "button" }, "解锁并下载");
    done.appendChild(doneMsg);
    done.appendChild(codeRow);
    done.appendChild(dlBtn);

    var manual = el("details", { "class": "esgpay-manual" });
    manual.appendChild(el("summary", null, "已有授权码 / 线下转账"));
    manual.appendChild(el("p", { "class": "esgpay-tip" }, "已付款、线下转账或收到过渡码，可直接粘贴授权码解锁："));
    var row = el("div", { "class": "esgpay-row" });
    var input = el("input", { type: "text", placeholder: "ESG-订单号-签名", autocomplete: "off" });
    var ok = el("button", { type: "button" }, "解锁");
    row.appendChild(input);
    row.appendChild(ok);
    var err = el("p", { "class": "esgpay-err" }, "");
    manual.appendChild(row);
    manual.appendChild(err);
    manual.appendChild(el("p", { "class": "esgpay-tip" })).innerHTML =
      '需要人工协助或批量授权：<a href="' + WA + '" target="_blank" rel="noopener">WhatsApp</a> · <a href="' + MAIL + '">邮件</a>';

    box.appendChild(close);
    box.appendChild(title);
    box.appendChild(sub);
    box.appendChild(price);
    box.appendChild(list);
    box.appendChild(lite);
    box.appendChild(payBox);
    box.appendChild(qrBox);
    box.appendChild(done);
    box.appendChild(manual);
    mask.appendChild(box);

    mask.addEventListener("click", function (e) { if (e.target === mask) finish(null); });
    document.addEventListener("keydown", onKeydown);

    function onKeydown(e) {
      if (e.key === "Escape" && overlay) finish(null);
    }

    function finish(code) {
      stopPolling();
      document.removeEventListener("keydown", onKeydown);
      if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
      overlay = null;
      var resolve = state.resolve;
      state.resolve = null;
      state.order = null;
      if (resolve) resolve(code || null);
    }

    function showError(msg) {
      err.textContent = msg || "";
    }

    function applyProduct(cfg) {
      var product = null;
      var items = (cfg && cfg.products) || [];
      for (var i = 0; i < items.length; i += 1) {
        if (items[i].id === (state.productId || DEFAULT_PRODUCT)) { product = items[i]; break; }
      }
      product = product || items[0] || null;
      state.product = product;
      var priceEl = box.querySelector("[data-price]");
      if (product) {
        priceEl.textContent = yuan(product.price);
        priceEl.parentNode.querySelector("span").textContent = "／企业 · " + (product.desc || "含 1 年版本更新");
      } else {
        priceEl.textContent = "—";
      }
      if (!cfg || !cfg.pay_enabled) {
        buy.textContent = "在线支付未开通，联系人工发码";
        payTip.textContent = "可直接 WhatsApp 或邮件联系，人工确认后发送授权码。";
      }
    }

    function setCode(code) {
      codeEl.textContent = code;
      done.hidden = false;
      qrBox.hidden = true;
      payBox.hidden = true;
      buy.disabled = false;
      track("pay_success", { source: state.source });
    }

    function startPolling() {
      stopPolling();
      state.polls = 0;
      state.timer = setInterval(pollOnce, POLL_MS);
      pollOnce();
    }

    function stopPolling() {
      if (state.timer) { clearInterval(state.timer); state.timer = null; }
    }

    function pollOnce() {
      if (!state.order) return;
      state.polls += 1;
      if (state.polls > MAX_POLLS) {
        stopPolling();
        status.textContent = "二维码已过期，请重新发起支付。";
        buy.disabled = false;
        buy.textContent = "重新获取二维码";
        buy.hidden = false;
        return;
      }
      fetch("/api/pay/order/" + encodeURIComponent(state.order), { cache: "no-store" })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok) return;
          var d = res.d;
          status.textContent = d.status === "PAID" ? "支付成功" : statusText(d.status);
          if (d.status === "PAID" && d.license) {
            stopPolling();
            setLicense(d.license);
            setCode(d.license);
          } else if (d.status === "AMOUNT_MISMATCH" || d.status === "CLOSED" || d.status === "FAILED") {
            stopPolling();
            status.textContent = statusText(d.status);
            buy.disabled = false;
            buy.hidden = false;
          }
        })
        .catch(function () { /* 网络抖动，下一轮再试 */ });
    }

    function statusText(s) {
      return { PENDING: "等待支付…", CLOSED: "订单已关闭，请重新支付", FAILED: "支付失败，请重试",
        REFUNDED: "已退款", AMOUNT_MISMATCH: "金额异常，请联系人工处理" }[s] || "等待支付…";
    }

    function createOrder() {
      buy.disabled = true;
      buy.textContent = "正在创建订单…";
      showError("");
      fetch("/api/pay/order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ productId: state.productId || DEFAULT_PRODUCT })
      }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      }).then(function (res) {
        if (!res.ok) {
          var detail = res.d && res.d.detail;
          var msg = typeof detail === "string" ? detail : (detail && detail.message) || "下单失败";
          showError(msg);
          manual.open = true;
          buy.disabled = false;
          buy.textContent = "重试微信支付";
          return;
        }
        var d = res.d;
        state.order = d.orderNo;
        qrBox.hidden = false;
        qrImg.src = d.qrDataUrl || "";
        qrImg.style.display = d.qrDataUrl ? "" : "none";
        qrLink.href = d.codeUrl || "#";
        orderNo.textContent = "订单号 " + d.orderNo + " · " + yuan(d.amount);
        status.textContent = "等待支付…";
        buy.hidden = true;
        track("pay_order", { source: state.source, value: Number(d.amount) });
        startPolling();
      }).catch(function () {
        showError("网络异常，请检查网络后重试。");
        buy.disabled = false;
        buy.textContent = "微信扫码支付";
      });
    }

    buy.addEventListener("click", function () {
      getConfig().then(function (cfg) {
        if (!cfg.pay_enabled) { manual.open = true; return; }
        createOrder();
      });
    });

    dlBtn.addEventListener("click", function () {
      var code = codeEl.textContent;
      if (!code) return;
      setLicense(code);
      finish(code);
    });

    copyBtn.addEventListener("click", function () {
      var code = codeEl.textContent;
      if (!code) return;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(code).then(function () { copyBtn.textContent = "已复制"; });
      } else {
        var tmp = el("textarea");
        tmp.value = code;
        document.body.appendChild(tmp);
        tmp.select();
        try { document.execCommand("copy"); copyBtn.textContent = "已复制"; } catch (_) { /* ignore */ }
        document.body.removeChild(tmp);
      }
    });

    ok.addEventListener("click", function () {
      var code = String(input.value || "").trim();
      if (!code) { showError("请先粘贴授权码"); return; }
      ok.disabled = true;
      showError("");
      verify(code).then(function (good) {
        ok.disabled = false;
        if (!good) { showError("授权码无效，请核对后重试，或联系人工协助。"); return; }
        setLicense(code);
        track("license_activate", { source: state.source });
        finish(code);
      });
    });

    input.addEventListener("keydown", function (e) { if (e.key === "Enter") ok.click(); });

    overlay = mask;
    document.body.appendChild(mask);
    getConfig().then(applyProduct);
  }

  function open(options) {
    options = options || {};
    if (overlay) return Promise.resolve(null);
    state.productId = options.productId || DEFAULT_PRODUCT;
    state.source = options.source || "web";
    injectStyles();
    return new Promise(function (resolve) {
      state.resolve = resolve;
      buildModal();
      track("pay_open", { source: state.source });
    });
  }

  function ensure(options) {
    options = options || {};
    var saved = storedLicense();
    if (saved) {
      return verify(saved).then(function (good) {
        if (good) return saved;
        clearLicense();
        return fromUrlOrModal(options);
      });
    }
    return fromUrlOrModal(options);
  }

  function fromUrlOrModal(options) {
    var qs = new URLSearchParams(location.search);
    var token = qs.get("token");
    if (token) {
      // 管理员口令：万能钥匙但不写入 localStorage
      return verify(token).then(function (good) { return good ? token : open(options); });
    }
    var code = qs.get("code") || qs.get("license");
    if (code) {
      return verify(code).then(function (good) {
        if (good) { setLicense(code); return code; }
        return open(options);
      });
    }
    return open(options);
  }

  window.ESGPay = {
    ensure: ensure,
    open: open,
    verify: verify,
    license: storedLicense,
    clear: clearLicense
  };
})();